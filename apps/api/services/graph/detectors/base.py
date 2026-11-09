from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


@dataclass
class EvidenceCandidate:
    source_chunk_id: UUID | None = None
    target_chunk_id: UUID | None = None
    source_version_id: UUID | None = None
    target_version_id: UUID | None = None
    page: int | None = None
    section_path: list | None = None
    span_start: int | None = None
    span_end: int | None = None
    method: str = ""
    score_raw: float | None = None
    score_normalized: float | None = None
    explanation: str | None = None
    content_hash: str | None = None

    def evidence_key(self) -> str:
        raw = (
            f"{self.source_chunk_id}|{self.target_chunk_id}|{self.method}|"
            f"{self.span_start}|{self.span_end}|{self.page}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


@dataclass
class RelationCandidate:
    source_document_id: UUID
    target_document_id: UUID
    source_version_id: UUID | None
    target_version_id: UUID | None
    relation_type: str
    directed: bool
    origin: str
    review_state: str
    score: float | None
    score_method: str | None
    algorithm_name: str
    algorithm_version: str
    detector_key: str
    model_id: str | None = None
    evidence: list[EvidenceCandidate] = field(default_factory=list)

    def edge_key(self) -> str:
        a, b = self.source_document_id, self.target_document_id
        if not self.directed and str(a) > str(b):
            a, b = b, a
        sa = self.source_version_id or ""
        tb = self.target_version_id or ""
        if not self.directed and str(a) != str(self.source_document_id):
            sa, tb = tb, sa
        raw = f"{a}|{b}|{sa}|{tb}|{self.relation_type}|{int(self.directed)}|{self.detector_key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]

    def canonical_endpoints(self) -> tuple[UUID, UUID, UUID | None, UUID | None]:
        if self.directed:
            return (
                self.source_document_id,
                self.target_document_id,
                self.source_version_id,
                self.target_version_id,
            )
        if str(self.source_document_id) <= str(self.target_document_id):
            return (
                self.source_document_id,
                self.target_document_id,
                self.source_version_id,
                self.target_version_id,
            )
        return (
            self.target_document_id,
            self.source_document_id,
            self.target_version_id,
            self.source_version_id,
        )


class RelationDetector(Protocol):
    detector_id: str
    algorithm_version: str
    is_enabled: bool

    def detect(self, document_id: UUID, context: dict) -> list[RelationCandidate]: ...
