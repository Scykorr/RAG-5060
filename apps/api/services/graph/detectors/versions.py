"""Deterministic version / family relations (SAME_FAMILY by sha256)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from apps.api.models import Document, DocumentVersion
from apps.api.services.graph.detectors.base import EvidenceCandidate, RelationCandidate


class VersionRelationDetector:
    detector_id = "version_family_v1"
    algorithm_version = "1"
    is_enabled = True

    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def detect(self, document_id: UUID, context: dict) -> list[RelationCandidate]:
        ver: DocumentVersion | None = context.get("version")
        if not ver or not ver.sha256:
            return []
        siblings = (
            self.db.query(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .filter(
                DocumentVersion.tenant_id == self.tenant_id,
                DocumentVersion.sha256 == ver.sha256,
                DocumentVersion.document_id != document_id,
                Document.deleted_at.is_(None),
                Document.status != "deleted",
                Document.active_version_id == DocumentVersion.id,
            )
            .limit(50)
            .all()
        )
        out: list[RelationCandidate] = []
        for other in siblings:
            out.append(
                RelationCandidate(
                    source_document_id=document_id,
                    target_document_id=other.document_id,
                    source_version_id=ver.id,
                    target_version_id=other.id,
                    relation_type="SAME_FAMILY",
                    directed=False,
                    origin="SYSTEM_RULE",
                    review_state="APPROVED",
                    score=None,
                    score_method=None,
                    algorithm_name=self.detector_id,
                    algorithm_version=self.algorithm_version,
                    detector_key=f"{self.detector_id}:sha256",
                    evidence=[
                        EvidenceCandidate(
                            source_version_id=ver.id,
                            target_version_id=other.id,
                            method="sha256_match",
                            explanation="Одинаковый SHA256 содержимого (семейство/дубликат).",
                        )
                    ],
                )
            )
        return out
