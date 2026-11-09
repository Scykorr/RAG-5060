from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from apps.api.auth import AuthUser
from apps.api.models import Chunk, Document, DocumentVersion, GraphBuildJob, GraphEdge, GraphEvidence
from apps.api.services.graph import acl, repository as repo
from apps.api.services.graph.settings import get_graph_limits


class GraphService:
    def __init__(self, db: Session, user: AuthUser):
        self.db = db
        self.user = user
        self.limits = get_graph_limits()

    def status(self) -> dict:
        vectors_ok = self._vectors_nonzero()
        edges = (
            self.db.query(func.count(GraphEdge.id))
            .filter(
                GraphEdge.tenant_id == self.user.tenant_id,
                GraphEdge.deleted_at.is_(None),
            )
            .scalar()
            or 0
        )
        docs = acl.visible_documents_query(self.db, self.user).count()
        out = {
            "enabled": self.limits.enabled,
            "llm_analysis_enabled": self.limits.llm_analysis_enabled,
            "documents_visible": docs,
            "edges_stored": int(edges),
            "vector_search": "ok" if vectors_ok else "DEGRADED_NO_VECTOR_SEARCH",
            "similarity_method": "token_jaccard_v1" if not vectors_ok else "embedding_cosine",
            "role": self.user.role,
        }
        if self.user.role == "admin":
            running = (
                self.db.query(func.count(GraphBuildJob.id))
                .filter(
                    GraphBuildJob.tenant_id == self.user.tenant_id,
                    GraphBuildJob.state.in_(("QUEUED", "RUNNING", "RETRYING")),
                )
                .scalar()
                or 0
            )
            out["jobs_active"] = int(running)
        return out

    def overview(self) -> dict:
        docs_q = acl.visible_documents_query(self.db, self.user)
        docs = docs_q.count()
        ready = docs_q.filter(Document.active_version_id.is_not(None)).count()
        edges = self._visible_edges_query().count()
        by_type = (
            self._visible_edges_query()
            .with_entities(GraphEdge.relation_type, func.count(GraphEdge.id))
            .group_by(GraphEdge.relation_type)
            .all()
        )
        return {
            "documents_visible": docs,
            "documents_indexed": ready,
            "edges_visible": edges,
            "edges_by_type": {t: int(c) for t, c in by_type},
            "vector_search": self.status()["vector_search"],
        }

    def nodes(
        self,
        *,
        search: str = "",
        prefix: str = "",
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict:
        limit = max(1, min(self.limits.hard_max_nodes, limit or self.limits.default_nodes))
        q = (
            acl.visible_documents_query(self.db, self.user)
            .join(DocumentVersion, DocumentVersion.id == Document.active_version_id, isouter=True)
        )
        if search.strip():
            like = f"%{search.strip()}%"
            q = q.filter(DocumentVersion.original_name.ilike(like))
        if prefix.strip():
            prefixes = [p.strip() for p in prefix.split(",") if p.strip()]
            if len(prefixes) == 1:
                q = q.filter(DocumentVersion.original_name.startswith(prefixes[0]))
            elif prefixes:
                q = q.filter(or_(*[DocumentVersion.original_name.startswith(p) for p in prefixes]))
        if cursor:
            try:
                q = q.filter(Document.created_at < datetime.fromisoformat(cursor))
            except ValueError:
                pass
        rows = q.order_by(Document.created_at.desc()).limit(limit + 1).all()
        truncated = len(rows) > limit
        rows = rows[:limit]
        nodes = [self._node_dto(d) for d in rows]
        next_cursor = rows[-1].created_at.isoformat() if truncated and rows else None
        return {
            "schema_version": "1.0",
            "nodes": nodes,
            "truncated": truncated,
            "has_more": truncated,
            "next_cursor": next_cursor,
            "applied_limits": {"max_nodes": limit},
        }

    def neighborhood(
        self,
        document_id: UUID,
        *,
        depth: int = 1,
        max_nodes: int | None = None,
        max_edges: int | None = None,
        relation_types: list[str] | None = None,
        min_score: float | None = None,
        review_states: list[str] | None = None,
    ) -> dict:
        depth = max(1, min(self.limits.max_depth, depth or self.limits.default_depth))
        max_nodes = max(1, min(self.limits.hard_max_nodes, max_nodes or self.limits.default_nodes))
        max_edges = max(1, min(self.limits.hard_max_edges, max_edges or self.limits.default_edges))
        center = acl.can_read_document(self.db, self.user, document_id)
        if not center:
            return {"code": "GRAPH_NODE_NOT_FOUND_OR_FORBIDDEN"}

        frontier = {document_id}
        seen_nodes = {document_id}
        seen_edges: set[UUID] = set()
        edge_rows: list[GraphEdge] = []

        for _ in range(depth):
            if not frontier or len(seen_nodes) >= max_nodes:
                break
            q = self._visible_edges_query().filter(
                or_(
                    GraphEdge.source_document_id.in_(list(frontier)),
                    GraphEdge.target_document_id.in_(list(frontier)),
                )
            )
            q = self._apply_edge_filters(q, relation_types, min_score, review_states)
            batch = q.limit(max_edges).all()
            frontier = set()
            for e in batch:
                if e.id in seen_edges:
                    continue
                if len(edge_rows) >= max_edges:
                    break
                # both ends must be visible (already via _visible_edges_query)
                seen_edges.add(e.id)
                edge_rows.append(e)
                for did in (e.source_document_id, e.target_document_id):
                    if did not in seen_nodes and len(seen_nodes) < max_nodes:
                        seen_nodes.add(did)
                        frontier.add(did)

        docs = (
            acl.visible_documents_query(self.db, self.user)
            .filter(Document.id.in_(list(seen_nodes)))
            .all()
        )
        truncated = len(edge_rows) >= max_edges or len(docs) >= max_nodes
        return {
            "schema_version": "1.0",
            "center_document_id": str(document_id),
            "nodes": [self._node_dto(d) for d in docs],
            "edges": [self._edge_dto(e) for e in edge_rows],
            "limits": {
                "requested_depth": depth,
                "max_nodes": max_nodes,
                "max_edges": max_edges,
            },
            "truncated": truncated,
            "has_more": truncated,
            "next_cursor": None,
        }

    def edges_for_nodes(
        self,
        node_ids: list[UUID],
        *,
        relation_types: list[str] | None = None,
        min_score: float | None = None,
        limit: int = 400,
    ) -> dict:
        limit = max(1, min(self.limits.hard_max_edges, limit))
        visible = acl.filter_visible_document_ids(self.db, self.user, set(node_ids))
        if not visible:
            return {"edges": [], "truncated": False, "has_more": False}
        q = self._visible_edges_query().filter(
            GraphEdge.source_document_id.in_(list(visible)),
            GraphEdge.target_document_id.in_(list(visible)),
        )
        q = self._apply_edge_filters(q, relation_types, min_score, None)
        rows = q.limit(limit + 1).all()
        truncated = len(rows) > limit
        rows = rows[:limit]
        return {
            "edges": [self._edge_dto(e) for e in rows],
            "truncated": truncated,
            "has_more": truncated,
            "applied_limits": {"max_edges": limit},
        }

    def document_relations(self, document_id: UUID, *, limit: int = 50, offset: int = 0) -> dict:
        if not acl.can_read_document(self.db, self.user, document_id):
            return {"code": "GRAPH_NODE_NOT_FOUND_OR_FORBIDDEN"}
        limit = max(1, min(200, limit))
        offset = max(0, offset)
        q = self._visible_edges_query().filter(
            or_(
                GraphEdge.source_document_id == document_id,
                GraphEdge.target_document_id == document_id,
            )
        )
        total = q.count()
        rows = q.order_by(GraphEdge.updated_at.desc()).offset(offset).limit(limit).all()
        return {"items": [self._edge_dto(e) for e in rows], "total": total, "limit": limit, "offset": offset}

    def get_edge(self, edge_id: UUID) -> dict | None:
        edge = acl.visible_edge(self.db, self.user, self.db.get(GraphEdge, edge_id))
        if not edge:
            return None
        dto = self._edge_dto(edge)
        dto["stale_reason"] = edge.stale_reason
        dto["algorithm_name"] = edge.algorithm_name
        dto["algorithm_version"] = edge.algorithm_version
        dto["model_id"] = edge.model_id
        dto["reviewed_at"] = edge.reviewed_at.isoformat() if edge.reviewed_at else None
        return dto

    def get_evidence(self, edge_id: UUID) -> dict | None:
        edge = acl.visible_edge(self.db, self.user, self.db.get(GraphEdge, edge_id))
        if not edge:
            return None
        rows = (
            self.db.query(GraphEvidence)
            .filter(
                GraphEvidence.edge_id == edge.id,
                GraphEvidence.tenant_id == self.user.tenant_id,
                GraphEvidence.status != "MISSING",
            )
            .order_by(GraphEvidence.created_at.asc())
            .limit(20)
            .all()
        )
        items = []
        for r in rows:
            item = {
                "id": str(r.id),
                "method": r.method,
                "page": r.page,
                "score_raw": r.score_raw,
                "score_normalized": r.score_normalized,
                "explanation": r.explanation,
                "status": r.status,
                "source_chunk_id": str(r.source_chunk_id) if r.source_chunk_id else None,
                "target_chunk_id": str(r.target_chunk_id) if r.target_chunk_id else None,
            }
            # Snippets only after ACL (edge already checked); truncate
            if r.source_chunk_id:
                ch = self.db.get(Chunk, r.source_chunk_id)
                if ch and ch.tenant_id == self.user.tenant_id:
                    item["source_snippet"] = (ch.text or "")[: self.limits.evidence_snippet_chars]
            if r.target_chunk_id:
                ch = self.db.get(Chunk, r.target_chunk_id)
                if ch and ch.tenant_id == self.user.tenant_id:
                    item["target_snippet"] = (ch.text or "")[: self.limits.evidence_snippet_chars]
            items.append(item)
        return {"edge_id": str(edge_id), "items": items}

    def review_edge(
        self, edge_id: UUID, *, action: str, reason: str | None, expected_version: int | None
    ) -> dict:
        if not acl.can_mutate_graph(self.user):
            return {"code": "GRAPH_FORBIDDEN"}
        edge = self.db.get(GraphEdge, edge_id)
        if not edge or edge.tenant_id != self.user.tenant_id or edge.deleted_at:
            return {"code": "GRAPH_EDGE_NOT_FOUND_OR_FORBIDDEN"}
        # Both ends must still be readable
        if not acl.visible_edge(self.db, self.user, edge):
            return {"code": "GRAPH_EDGE_NOT_FOUND_OR_FORBIDDEN"}
        if expected_version is not None and edge.payload_version != expected_version:
            return {"code": "GRAPH_REVIEW_CONFLICT", "payload_version": edge.payload_version}
        old = edge.review_state
        action_u = action.upper()
        mapping = {
            "APPROVE": "APPROVED",
            "REJECT": "REJECTED",
            "REQUEST_CHANGES": "PENDING_REVIEW",
        }
        if action_u not in mapping:
            return {"code": "GRAPH_VALIDATION_ERROR"}
        edge.review_state = mapping[action_u]
        edge.reviewed_by = self.user.id
        edge.reviewed_at = datetime.now(timezone.utc)
        edge.payload_version = (edge.payload_version or 1) + 1
        repo.append_review_event(
            self.db,
            tenant_id=self.user.tenant_id,
            edge_id=edge.id,
            actor_id=self.user.id,
            action=action_u,
            old_state=old,
            new_state=edge.review_state,
            reason=reason,
        )
        self.db.commit()
        return {"ok": True, "edge": self.get_edge(edge.id)}

    def create_manual_edge(
        self,
        *,
        source_document_id: UUID,
        target_document_id: UUID,
        relation_type: str,
        directed: bool,
        reason: str,
    ) -> dict:
        if not acl.can_mutate_graph(self.user):
            return {"code": "GRAPH_FORBIDDEN"}
        src = acl.can_read_document(self.db, self.user, source_document_id)
        tgt = acl.can_read_document(self.db, self.user, target_document_id)
        if not src or not tgt:
            return {"code": "GRAPH_NODE_NOT_FOUND_OR_FORBIDDEN"}
        from apps.api.services.graph.detectors.base import EvidenceCandidate, RelationCandidate

        cand = RelationCandidate(
            source_document_id=source_document_id,
            target_document_id=target_document_id,
            source_version_id=src.active_version_id,
            target_version_id=tgt.active_version_id,
            relation_type=relation_type if relation_type else "MANUAL_LINK",
            directed=directed,
            origin="HUMAN",
            review_state="APPROVED",
            score=None,
            score_method=None,
            algorithm_name="manual",
            algorithm_version="1",
            detector_key="manual",
            evidence=[
                EvidenceCandidate(
                    source_version_id=src.active_version_id,
                    target_version_id=tgt.active_version_id,
                    method="human",
                    explanation=(reason or "")[:500],
                )
            ],
        )
        edge = repo.upsert_candidate(
            self.db, tenant_id=self.user.tenant_id, candidate=cand, run_id=None
        )
        edge.created_by = self.user.id
        edge.reviewed_by = self.user.id
        edge.reviewed_at = datetime.now(timezone.utc)
        repo.append_review_event(
            self.db,
            tenant_id=self.user.tenant_id,
            edge_id=edge.id,
            actor_id=self.user.id,
            action="MANUAL_CREATE",
            old_state=None,
            new_state="APPROVED",
            reason=reason,
        )
        self.db.commit()
        return {"ok": True, "edge": self.get_edge(edge.id)}

    def _visible_edges_query(self):
        # Join both documents visible in tenant / not deleted
        src = Document.__table__.alias("src_doc")
        tgt = Document.__table__.alias("tgt_doc")
        q = (
            self.db.query(GraphEdge)
            .join(src, src.c.id == GraphEdge.source_document_id)
            .join(tgt, tgt.c.id == GraphEdge.target_document_id)
            .filter(
                GraphEdge.tenant_id == self.user.tenant_id,
                GraphEdge.deleted_at.is_(None),
                GraphEdge.review_state != "REJECTED",
                src.c.tenant_id == self.user.tenant_id,
                tgt.c.tenant_id == self.user.tenant_id,
                src.c.deleted_at.is_(None),
                tgt.c.deleted_at.is_(None),
                src.c.status != "deleted",
                tgt.c.status != "deleted",
            )
        )
        if self.user.role != "admin":
            q = q.filter(GraphEdge.review_state != "STALE")
        return q

    def _apply_edge_filters(self, q, relation_types, min_score, review_states):
        if relation_types:
            allowed = {
                "SEMANTIC_SIMILAR",
                "EXPLICIT_REFERENCE",
                "SAME_FAMILY",
                "SHARES_ENTITY",
                "SUPERSEDES",
                "EXTENDS",
                "POTENTIAL_CONTRADICTION",
                "MANUAL_LINK",
            }
            types = [t for t in relation_types if t in allowed]
            if types:
                q = q.filter(GraphEdge.relation_type.in_(types))
        if min_score is not None:
            q = q.filter(or_(GraphEdge.score.is_(None), GraphEdge.score >= float(min_score)))
        if review_states:
            q = q.filter(GraphEdge.review_state.in_(review_states))
        return q

    def _node_dto(self, d: Document) -> dict:
        ver = self.db.get(DocumentVersion, d.active_version_id) if d.active_version_id else None
        rel_count = (
            self._visible_edges_query()
            .filter(
                or_(
                    GraphEdge.source_document_id == d.id,
                    GraphEdge.target_document_id == d.id,
                )
            )
            .count()
        )
        status = "PUBLISHED" if d.active_version_id else "NOT_INDEXED"
        if ver and ver.indexed_chunk_count == 0:
            status = "NO_EMBEDDINGS"
        label = ver.original_name if ver else str(d.id)
        return {
            "id": f"doc:{d.id}",
            "type": "DOCUMENT",
            "document_id": str(d.id),
            "active_version_id": str(d.active_version_id) if d.active_version_id else None,
            "label": label,
            "status": status,
            "mime_type": ver.mime_type if ver else None,
            "accessible": True,
            "relation_count_visible": int(rel_count),
            "size_class": "medium",
        }

    def _edge_dto(self, e: GraphEdge) -> dict:
        ev_count = (
            self.db.query(func.count(GraphEvidence.id))
            .filter(GraphEvidence.edge_id == e.id, GraphEvidence.status == "VALID")
            .scalar()
            or 0
        )
        return {
            "id": f"edge:{e.id}",
            "edge_id": str(e.id),
            "source": f"doc:{e.source_document_id}",
            "target": f"doc:{e.target_document_id}",
            "source_document_id": str(e.source_document_id),
            "target_document_id": str(e.target_document_id),
            "relation_type": e.relation_type,
            "directed": e.directed,
            "origin": e.origin,
            "review_state": e.review_state,
            "score": e.score,
            "score_method": e.score_method,
            "evidence_count": int(ev_count),
            "payload_version": e.payload_version,
        }

    def _vectors_nonzero(self) -> bool:
        """Detect whether Qdrant holds real embeddings (not all-zero stub)."""
        try:
            from qdrant_client import QdrantClient

            from apps.api.settings import get_settings

            settings = get_settings()
            client = QdrantClient(url=settings.qdrant_url, timeout=3)
            if settings.qdrant_collection not in {c.name for c in client.get_collections().collections}:
                return False
            pts, _ = client.scroll(collection_name=settings.qdrant_collection, limit=3, with_vectors=True)
            for p in pts:
                vec = p.vector
                if isinstance(vec, dict):
                    vec = next(iter(vec.values()), [])
                if vec and any(abs(float(x)) > 1e-9 for x in vec[:16]):
                    return True
            return False
        except Exception:  # noqa: BLE001
            return False
