"""Explicit reference detector — unique title/filename mentions in chunk text."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.orm import Session

from apps.api.models import Chunk, Document, DocumentVersion
from apps.api.services.graph.detectors.base import EvidenceCandidate, RelationCandidate

_TITLE_MIN = 12
_BOILERPLATE = re.compile(
    r"^(содержание|оглавление|титульный|copyright|все права|страница\s*\d+)$",
    re.I,
)


def _title_from_filename(name: str) -> str:
    base = name.rsplit(".", 1)[0]
    # classic_Author_Title_frag → Title
    parts = base.split("_")
    if len(parts) >= 3:
        # drop pack + author-ish first two tokens when pack_ prefix present
        title_parts = parts[2:]
        title = " ".join(title_parts)
    else:
        title = base.replace("_", " ")
    title = re.sub(r"\s*фрагмент\s*\d+\s*$", "", title, flags=re.I).strip()
    return title


def _is_unique_enough(title: str, catalog: dict[str, list[UUID]]) -> bool:
    key = title.casefold()
    return len(key) >= _TITLE_MIN and len(catalog.get(key, [])) == 1 and not _BOILERPLATE.match(title)


class ExplicitReferenceDetector:
    detector_id = "explicit_reference_v1"
    algorithm_version = "1"
    is_enabled = True

    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def detect(self, document_id: UUID, context: dict) -> list[RelationCandidate]:
        ver: DocumentVersion | None = context.get("version")
        chunks: list[Chunk] = context.get("chunks") or []
        catalog: dict[str, list[UUID]] = context.get("title_catalog") or {}
        if not ver or not chunks or not catalog:
            return []

        # Build searchable needles: unique titles only
        needles: list[tuple[str, UUID]] = []
        for title_key, doc_ids in catalog.items():
            if len(doc_ids) != 1:
                continue
            tid = doc_ids[0]
            if tid == document_id:
                continue
            if len(title_key) < _TITLE_MIN:
                continue
            needles.append((title_key, tid))

        out: list[RelationCandidate] = []
        seen_targets: set[UUID] = set()
        for ch in chunks[:40]:
            text_l = (ch.text or "").casefold()
            if len(text_l) < 40:
                continue
            for title_key, target_id in needles:
                if target_id in seen_targets:
                    continue
                # Require distinctive phrase; avoid single common word
                if title_key not in text_l:
                    continue
                # Prefer quoted or «title» style; allow plain if long enough
                idx = text_l.find(title_key)
                if idx < 0:
                    continue
                # Ambiguous short match guard already via length
                target_ver = (
                    self.db.query(DocumentVersion)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .filter(
                        Document.id == target_id,
                        Document.tenant_id == self.tenant_id,
                        Document.deleted_at.is_(None),
                        Document.active_version_id == DocumentVersion.id,
                    )
                    .first()
                )
                if not target_ver:
                    continue
                seen_targets.add(target_id)
                out.append(
                    RelationCandidate(
                        source_document_id=document_id,
                        target_document_id=target_id,
                        source_version_id=ver.id,
                        target_version_id=target_ver.id,
                        relation_type="EXPLICIT_REFERENCE",
                        directed=True,
                        origin="SYSTEM_RULE",
                        review_state="PROPOSED",
                        score=None,
                        score_method=None,
                        algorithm_name=self.detector_id,
                        algorithm_version=self.algorithm_version,
                        detector_key=f"{self.detector_id}:title",
                        evidence=[
                            EvidenceCandidate(
                                source_chunk_id=ch.id,
                                source_version_id=ver.id,
                                target_version_id=target_ver.id,
                                page=ch.page_start,
                                span_start=idx,
                                span_end=idx + len(title_key),
                                method="title_mention",
                                explanation=f"Упоминание уникального названия «{title_key[:80]}» в тексте.",
                            )
                        ],
                    )
                )
                if len(out) >= 25:
                    return out
        return out


def build_title_catalog(db: Session, tenant_id: UUID) -> dict[str, list[UUID]]:
    rows = (
        db.query(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .filter(
            Document.tenant_id == tenant_id,
            Document.deleted_at.is_(None),
            Document.status != "deleted",
            Document.active_version_id == DocumentVersion.id,
        )
        .all()
    )
    catalog: dict[str, list[UUID]] = {}
    for ver in rows:
        title = _title_from_filename(ver.original_name)
        if not _is_unique_enough(title, {title.casefold(): [ver.document_id]}):
            # provisional uniqueness check after fill
            pass
        key = title.casefold()
        if len(key) < _TITLE_MIN or _BOILERPLATE.match(title):
            continue
        catalog.setdefault(key, []).append(ver.document_id)
    return catalog
