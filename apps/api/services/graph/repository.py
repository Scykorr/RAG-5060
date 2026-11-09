from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from apps.api.models import GraphEdge, GraphEvidence, GraphReviewEvent
from apps.api.services.graph.detectors.base import RelationCandidate


def upsert_candidate(
    db: Session,
    *,
    tenant_id: UUID,
    candidate: RelationCandidate,
    run_id: UUID | None,
) -> GraphEdge:
    src, tgt, sv, tv = candidate.canonical_endpoints()
    edge_key = candidate.edge_key()
    now = datetime.now(timezone.utc)
    existing = (
        db.query(GraphEdge)
        .filter(GraphEdge.tenant_id == tenant_id, GraphEdge.edge_key == edge_key)
        .one_or_none()
    )
    if existing:
        if existing.review_state not in ("APPROVED", "REJECTED"):
            existing.review_state = candidate.review_state
        existing.score = candidate.score
        existing.score_method = candidate.score_method
        existing.source_version_id = sv
        existing.target_version_id = tv
        existing.run_id = run_id
        existing.stale_reason = None
        existing.deleted_at = None
        existing.updated_at = now
        edge = existing
    else:
        edge = GraphEdge(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            source_document_id=src,
            target_document_id=tgt,
            source_version_id=sv,
            target_version_id=tv,
            relation_type=candidate.relation_type,
            directed=candidate.directed,
            origin=candidate.origin,
            review_state=candidate.review_state,
            score=candidate.score,
            score_method=candidate.score_method,
            algorithm_name=candidate.algorithm_name,
            algorithm_version=candidate.algorithm_version,
            model_id=candidate.model_id,
            run_id=run_id,
            detector_key=candidate.detector_key,
            edge_key=edge_key,
        )
        db.add(edge)
        db.flush()

    for ev in candidate.evidence:
        ekey = ev.evidence_key()
        row = (
            db.query(GraphEvidence)
            .filter(GraphEvidence.edge_id == edge.id, GraphEvidence.evidence_key == ekey)
            .one_or_none()
        )
        if row:
            row.status = "VALID"
            row.score_raw = ev.score_raw
            row.score_normalized = ev.score_normalized
            row.explanation = ev.explanation
            continue
        db.add(
            GraphEvidence(
                id=uuid.uuid4(),
                edge_id=edge.id,
                tenant_id=tenant_id,
                source_document_version_id=ev.source_version_id,
                target_document_version_id=ev.target_version_id,
                source_chunk_id=ev.source_chunk_id,
                target_chunk_id=ev.target_chunk_id,
                page=ev.page,
                section_path=ev.section_path,
                span_start=ev.span_start,
                span_end=ev.span_end,
                method=ev.method,
                score_raw=ev.score_raw,
                score_normalized=ev.score_normalized,
                content_hash=ev.content_hash,
                explanation=ev.explanation,
                status="VALID",
                evidence_key=ekey,
            )
        )
    db.flush()
    return edge


def mark_stale_for_version(
    db: Session, *, tenant_id: UUID, document_id: UUID, old_version_id: UUID, reason: str
) -> int:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(GraphEdge)
        .filter(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.deleted_at.is_(None),
            (
                (GraphEdge.source_document_id == document_id)
                & (GraphEdge.source_version_id == old_version_id)
            )
            | (
                (GraphEdge.target_document_id == document_id)
                & (GraphEdge.target_version_id == old_version_id)
            ),
        )
        .all()
    )
    for e in rows:
        e.review_state = "STALE"
        e.stale_reason = reason
        e.updated_at = now
    return len(rows)


def soft_hide_document_edges(db: Session, *, tenant_id: UUID, document_id: UUID) -> int:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(GraphEdge)
        .filter(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.deleted_at.is_(None),
            (GraphEdge.source_document_id == document_id)
            | (GraphEdge.target_document_id == document_id),
        )
        .all()
    )
    for e in rows:
        e.deleted_at = now
        e.updated_at = now
    return len(rows)


def append_review_event(
    db: Session,
    *,
    tenant_id: UUID,
    edge_id: UUID,
    actor_id: UUID | None,
    action: str,
    old_state: str | None,
    new_state: str | None,
    reason: str | None,
) -> None:
    db.add(
        GraphReviewEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            edge_id=edge_id,
            actor_id=actor_id,
            action=action,
            old_state=old_state,
            new_state=new_state,
            reason=reason,
        )
    )
