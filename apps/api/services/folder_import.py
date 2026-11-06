"""Safe folder import from configured inbox (bind-mounted host folder)."""

from __future__ import annotations

import hashlib
import mimetypes
import threading
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from apps.api.db import SessionLocal
from apps.api.models import Document, DocumentVersion
from apps.api.services import corpus_packs
from apps.api.services.ingestion import create_upload
from apps.api.settings import get_settings

ALLOWED_EXT = {".pdf", ".docx", ".txt"}
DENY_NAMES = {"_manifest.txt", "manifest.txt", ".gitkeep"}

# In-process status for the latest folder import (MVP; single concurrent import).
_import_lock = threading.Lock()
_import_state: dict = {
    "running": False,
    "batch_id": None,
    "total": 0,
    "done": 0,
    "skipped": 0,
    "failed": 0,
    "current": None,
    "items": [],
    "error": None,
    "pack": None,
}


def import_state() -> dict:
    with _import_lock:
        return dict(_import_state)


def inbox_root() -> Path:
    settings = get_settings()
    root = Path(settings.import_inbox).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_under_inbox(subdir: str | None = None) -> Path:
    root = inbox_root()
    if not subdir or subdir in (".", "/"):
        return root
    # Normalize separators; forbid absolute and escape
    cleaned = subdir.replace("\\", "/").lstrip("/")
    if ".." in Path(cleaned).parts:
        raise ValueError("subdir path escape not allowed")
    target = (root / cleaned).resolve()
    if root not in target.parents and target != root:
        raise ValueError("subdir outside inbox")
    if not target.is_dir():
        raise FileNotFoundError(f"folder not found: {cleaned}")
    return target


def list_inbox_files(subdir: str | None = None, recursive: bool = True) -> list[dict]:
    base = resolve_under_inbox(subdir)
    files: list[dict] = []
    paths = base.rglob("*") if recursive else base.glob("*")
    for p in sorted(paths):
        if not p.is_file():
            continue
        if p.suffix.lower() not in ALLOWED_EXT:
            continue
        if p.name.lower() in DENY_NAMES or p.name.startswith("_"):
            continue
        rel = p.relative_to(inbox_root()).as_posix()
        st = p.stat()
        files.append(
            {
                "relative_path": rel,
                "name": p.name,
                "size": st.st_size,
                "suffix": p.suffix.lower(),
            }
        )
    return files


def _mime_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    if path.suffix.lower() == ".txt":
        return "text/plain"
    if path.suffix.lower() == ".pdf":
        return "application/pdf"
    if path.suffix.lower() == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _already_imported(db: Session, tenant_id: uuid.UUID, digest: str) -> bool:
    row = (
        db.query(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .filter(
            DocumentVersion.tenant_id == tenant_id,
            DocumentVersion.sha256 == digest,
            Document.deleted_at.is_(None),
            Document.status != "deleted",
        )
        .first()
    )
    return row is not None


def _run_batch(
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    files: list[Path],
    skip_existing: bool,
    batch_id: str,
    pack: dict,
) -> None:
    global _import_state
    settings = get_settings()
    max_b = settings.upload_max_mb * 1024 * 1024
    prefix = pack.get("prefix") or ""
    with _import_lock:
        _import_state.update(
            {
                "running": True,
                "batch_id": batch_id,
                "total": len(files),
                "done": 0,
                "skipped": 0,
                "failed": 0,
                "current": None,
                "items": [],
                "error": None,
                "pack": pack,
            }
        )
    try:
        for path in files:
            rel = path.relative_to(inbox_root()).as_posix()
            with _import_lock:
                _import_state["current"] = rel
            item: dict = {"path": rel, "status": "pending"}
            try:
                data = path.read_bytes()
                if len(data) > max_b:
                    item.update({"status": "failed", "error": f"exceeds {settings.upload_max_mb} MB"})
                    with _import_lock:
                        _import_state["failed"] += 1
                        _import_state["items"].append(item)
                    continue
                digest = _sha256(data)
                stamped = corpus_packs.apply_prefix_to_filename(path.name, prefix)
                db = SessionLocal()
                try:
                    if skip_existing and _already_imported(db, tenant_id, digest):
                        item.update({"status": "skipped", "reason": "duplicate_sha256", "filename": stamped})
                        with _import_lock:
                            _import_state["skipped"] += 1
                            _import_state["items"].append(item)
                        continue
                    doc, version, job = create_upload(
                        db,
                        tenant_id=tenant_id,
                        owner_id=owner_id,
                        filename=stamped,
                        mime=_mime_for(path),
                        data=data,
                    )
                    item.update(
                        {
                            "status": "ok",
                            "document_id": str(doc.id),
                            "job_id": str(job.id),
                            "job_stage": job.stage,
                            "version_status": version.status,
                            "filename": stamped,
                            "prefix": prefix,
                        }
                    )
                    with _import_lock:
                        _import_state["done"] += 1
                        _import_state["items"].append(item)
                finally:
                    db.close()
            except Exception as exc:  # noqa: BLE001
                item.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]})
                with _import_lock:
                    _import_state["failed"] += 1
                    _import_state["items"].append(item)
        # Refresh active retrieval prefixes after batch (local, offline).
        db = SessionLocal()
        try:
            active = corpus_packs.sync_active_retrieval_prefixes(db)
            db.commit()
            with _import_lock:
                _import_state["retrieval_filename_prefix"] = active
            try:
                from apps.api.services.quality_trigger import after_folder_import_done

                after_folder_import_done(tenant_id)
            except Exception:  # noqa: BLE001
                pass
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        with _import_lock:
            _import_state["error"] = f"{type(exc).__name__}: {exc}"[:400]
    finally:
        with _import_lock:
            _import_state["running"] = False
            _import_state["current"] = None


def _collect_files(subdir: str | None, recursive: bool = True) -> list[Path]:
    entries = list_inbox_files(subdir, recursive=recursive)
    root = inbox_root()
    safe: list[Path] = []
    for e in entries:
        rp = (root / e["relative_path"]).resolve()
        if rp.is_file() and (root in rp.parents or rp.parent == root):
            safe.append(rp)
    return safe


def preview_folder_import(*, subdir: str | None = None, recursive: bool = True) -> dict:
    """Auto-detect category suggestion + existing options; no import yet."""
    safe = _collect_files(subdir, recursive=recursive)
    root = inbox_root()
    if not safe:
        return {
            "status": "empty",
            "total": 0,
            "inbox": str(root),
            "subdir": subdir or "",
            "suggestion": None,
            "options": [],
        }
    db = SessionLocal()
    try:
        suggestion = corpus_packs.suggest_pack(db, subdir=subdir, files=safe)
    finally:
        db.close()
    return {
        "status": "ready",
        "total": len(safe),
        "inbox": str(root),
        "subdir": subdir or "",
        "host_hint": get_settings().import_inbox_host_hint,
        "suggestion": suggestion,
        "options": suggestion.get("options") or [],
        "proposed_new": suggestion.get("proposed_new"),
    }


def start_folder_import(
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    subdir: str | None = None,
    recursive: bool = True,
    skip_existing: bool = True,
    pack_mode: str | None = None,
    pack_id: str | None = None,
    pack_label: str | None = None,
) -> dict:
    with _import_lock:
        if _import_state.get("running"):
            raise RuntimeError("folder import already running")
    safe = _collect_files(subdir, recursive=recursive)
    root = inbox_root()
    batch_id = str(uuid.uuid4())
    if not safe:
        return {
            "batch_id": batch_id,
            "status": "empty",
            "total": 0,
            "inbox": str(root),
            "subdir": subdir or "",
            "pack": None,
        }

    db = SessionLocal()
    try:
        if pack_mode:
            pack = corpus_packs.confirm_pack_choice(
                db,
                mode=pack_mode,
                pack_id=pack_id,
                label=pack_label,
                subdir=subdir,
            )
        else:
            # Fallback: auto-confirm suggestion (API clients without UI)
            pack = corpus_packs.resolve_pack(db, subdir=subdir, files=safe)
        db.commit()
    finally:
        db.close()

    t = threading.Thread(
        target=_run_batch,
        kwargs={
            "tenant_id": tenant_id,
            "owner_id": owner_id,
            "files": safe,
            "skip_existing": skip_existing,
            "batch_id": batch_id,
            "pack": pack,
        },
        daemon=True,
    )
    t.start()
    return {
        "batch_id": batch_id,
        "status": "running",
        "total": len(safe),
        "inbox": str(root),
        "subdir": subdir or "",
        "host_hint": get_settings().import_inbox_host_hint,
        "pack": pack,
    }
