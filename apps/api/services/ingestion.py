from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from apps.api.models import Chunk, Document, DocumentVersion, IngestionJob
from apps.api.services.storage import data_root
from apps.api.settings import get_settings


def _maybe_qdrant_upsert(version: DocumentVersion, pieces: list[dict]) -> int:
    settings = get_settings()
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qmodels
    except ImportError:
        return 0
    try:
        client = QdrantClient(url=settings.qdrant_url, timeout=5)
        names = {c.name for c in client.get_collections().collections}
        if settings.qdrant_collection not in names:
            client.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=qmodels.VectorParams(
                    size=settings.embedding_dim,
                    distance=qmodels.Distance.COSINE,
                ),
            )
        points = []
        for i, piece in enumerate(pieces):
            points.append(
                qmodels.PointStruct(
                    id=f"{version.id}-{i}",
                    vector=[0.0] * settings.embedding_dim,
                    payload={
                        "tenant_id": str(version.tenant_id),
                        "document_id": str(version.document_id),
                        "version_id": str(version.id),
                        "ordinal": i,
                    },
                )
            )
        if points:
            client.upsert(collection_name=settings.qdrant_collection, points=points)
        return len(points)
    except Exception:
        return 0


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_text(path: Path, mime: str) -> tuple[str, str]:
    """Return (text, status) status ready|needs_ocr|failed."""
    suffix = path.suffix.lower()
    if suffix == ".txt" or mime.startswith("text/"):
        raw = path.read_bytes()
        for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
            try:
                return raw.decode(enc), "ready"
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace"), "ready"
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            parts = []
            for i, page in enumerate(reader.pages):
                t = page.extract_text() or ""
                if t.strip():
                    parts.append(f"[[page:{i+1}]]\n{t}")
            text = "\n\n".join(parts).strip()
            if not text:
                return "", "needs_ocr"
            return text, "ready"
        except Exception as exc:  # noqa: BLE001
            return "", f"failed:{type(exc).__name__}"
    if suffix == ".docx":
        try:
            import zipfile
            from xml.etree import ElementTree as ET

            with zipfile.ZipFile(path) as zf:
                xml = zf.read("word/document.xml")
            root = ET.fromstring(xml)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            texts = [n.text for n in root.findall(".//w:t", ns) if n.text]
            text = "\n".join(texts).strip()
            return (text, "ready") if text else ("", "failed:empty")
        except Exception as exc:  # noqa: BLE001
            return "", f"failed:{type(exc).__name__}"
    return "", "failed:unsupported"


def chunk_text(text: str, target_chars: int = 1400, overlap: int = 200) -> list[dict]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[dict] = []
    buf = ""
    page = None
    for para in paras:
        m = re.match(r"\[\[page:(\d+)\]\]\s*", para)
        if m:
            page = int(m.group(1))
            para = para[m.end() :]
        if len(buf) + len(para) + 2 <= target_chars:
            buf = f"{buf}\n\n{para}".strip()
        else:
            if buf:
                chunks.append({"text": buf, "page": page})
            if overlap and chunks:
                tail = chunks[-1]["text"][-overlap:]
                buf = f"{tail}\n\n{para}".strip()
            else:
                buf = para
    if buf:
        chunks.append({"text": buf, "page": page})
    return chunks


def process_job(db: Session, job_id: uuid.UUID) -> None:
    job = db.get(IngestionJob, job_id)
    if not job:
        return
    version = db.get(DocumentVersion, job.version_id)
    doc = db.get(Document, job.document_id)
    if not version or not doc:
        return
    job.stage = "parsing"
    job.started_at = datetime.now(timezone.utc)
    job.attempts += 1
    db.commit()

    path = Path(version.storage_key)
    if not path.is_absolute():
        path = data_root() / path
    text, st = extract_text(path, version.mime_type)
    if st.startswith("failed") or st == "needs_ocr":
        version.status = "needs_ocr" if st == "needs_ocr" else "failed"
        job.stage = "failed"
        job.safe_error = st
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    extracted = data_root() / "extracted" / f"{version.id}.txt"
    extracted.write_text(text, encoding="utf-8")

    job.stage = "chunking"
    version.status = "chunking"
    db.commit()

    pieces = chunk_text(text)
    # clear old chunks for version
    db.query(Chunk).filter(Chunk.version_id == version.id).delete()
    for i, piece in enumerate(pieces):
        db.add(
            Chunk(
                tenant_id=version.tenant_id,
                document_id=version.document_id,
                version_id=version.id,
                ordinal=i,
                text=piece["text"],
                page_start=piece.get("page"),
                page_end=piece.get("page"),
                token_count=max(1, len(piece["text"]) // 4),
            )
        )
    version.expected_chunk_count = len(pieces)
    indexed = _maybe_qdrant_upsert(version, pieces)
    version.indexed_chunk_count = indexed if indexed else len(pieces)
    version.status = "ready"
    doc.active_version_id = version.id
    doc.status = "active"
    job.stage = "ready"
    job.progress = 100
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    try:
        from apps.api.services.quality_trigger import after_ingestion_ready

        after_ingestion_ready(job.tenant_id)
    except Exception:  # noqa: BLE001
        pass


def create_upload(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    filename: str,
    mime: str,
    data: bytes,
) -> tuple[Document, DocumentVersion, IngestionJob]:
    digest = _sha256(data)
    doc = Document(tenant_id=tenant_id, owner_id=owner_id, status="processing")
    db.add(doc)
    db.flush()
    rel = Path("originals") / str(tenant_id) / f"{doc.id}_{filename}"
    abs_path = data_root() / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(data)
    version = DocumentVersion(
        tenant_id=tenant_id,
        document_id=doc.id,
        version_number=1,
        sha256=digest,
        original_name=filename,
        mime_type=mime or "application/octet-stream",
        storage_key=str(abs_path),
        status="pending",
    )
    db.add(version)
    db.flush()
    job = IngestionJob(
        tenant_id=tenant_id,
        document_id=doc.id,
        version_id=version.id,
        stage="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(doc)
    db.refresh(version)
    db.refresh(job)
    process_job(db, job.id)
    db.refresh(doc)
    db.refresh(version)
    db.refresh(job)
    return doc, version, job
