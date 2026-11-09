"""ACL adapter — mirrors documents router (tenant + soft-delete). No access_groups in DB yet."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, Query

from apps.api.auth import AuthUser
from apps.api.models import Document, GraphEdge


def visible_documents_query(db: Session, user: AuthUser) -> Query:
    return db.query(Document).filter(
        Document.tenant_id == user.tenant_id,
        Document.deleted_at.is_(None),
        Document.status != "deleted",
    )


def can_read_document(db: Session, user: AuthUser, document_id: UUID) -> Document | None:
    doc = db.get(Document, document_id)
    if not doc or doc.tenant_id != user.tenant_id or doc.deleted_at or doc.status == "deleted":
        return None
    return doc


def can_mutate_graph(user: AuthUser) -> bool:
    return user.role == "admin"


def visible_edge(
    db: Session, user: AuthUser, edge: GraphEdge | None
) -> GraphEdge | None:
    if not edge or edge.tenant_id != user.tenant_id or edge.deleted_at:
        return None
    if edge.review_state == "REJECTED" and user.role != "admin":
        return None
    src = can_read_document(db, user, edge.source_document_id)
    tgt = can_read_document(db, user, edge.target_document_id)
    if not src or not tgt:
        return None
    return edge


def filter_visible_document_ids(db: Session, user: AuthUser, ids: set[UUID]) -> set[UUID]:
    if not ids:
        return set()
    rows = (
        visible_documents_query(db, user)
        .filter(Document.id.in_(list(ids)))
        .with_entities(Document.id)
        .all()
    )
    return {r[0] for r in rows}
