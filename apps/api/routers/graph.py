"""Graph Explorer API — ACL-aware document connectivity."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import AuthUser, get_current_user, require_admin
from apps.api.db import get_db
from apps.api.models import Document, GraphBuildJob
from apps.api.services.graph import acl
from apps.api.services.graph.jobs import enqueue_analyze, process_job
from apps.api.services.graph.service import GraphService

router = APIRouter(prefix="/api/graph", tags=["graph"])


def _err(code: str, message: str, *, status: int = 404, retryable: bool = False):
    raise HTTPException(
        status_code=status,
        detail={"code": code, "message": message, "retryable": retryable},
    )


@router.get("/status")
def graph_status(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return GraphService(db, user).status()


@router.get("/overview")
def graph_overview(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return GraphService(db, user).overview()


@router.get("/nodes")
def graph_nodes(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    search: str = "",
    prefix: str = "",
    limit: int = Query(default=200, ge=1, le=500),
    cursor: str | None = None,
):
    return GraphService(db, user).nodes(search=search, prefix=prefix, limit=limit, cursor=cursor)


@router.get("/edges")
def graph_edges(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    node_ids: str = Query(default="", description="Comma-separated document UUIDs"),
    relation_types: str = "",
    min_score: float | None = None,
    limit: int = Query(default=400, ge=1, le=1500),
):
    ids: list[uuid.UUID] = []
    for part in node_ids.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(uuid.UUID(part))
        except ValueError:
            _err("GRAPH_VALIDATION_ERROR", "Invalid node id", status=400)
        if len(ids) > 500:
            _err("GRAPH_LIMIT_EXCEEDED", "Too many node_ids", status=400)
    types = [t.strip() for t in relation_types.split(",") if t.strip()] or None
    return GraphService(db, user).edges_for_nodes(
        ids, relation_types=types, min_score=min_score, limit=limit
    )


@router.get("/neighborhood/{document_id}")
def graph_neighborhood(
    document_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    depth: int = Query(default=1, ge=1, le=3),
    max_nodes: int = Query(default=200, ge=1, le=500),
    max_edges: int = Query(default=400, ge=1, le=1500),
    relation_types: str = "",
    min_score: float | None = None,
):
    types = [t.strip() for t in relation_types.split(",") if t.strip()] or None
    out = GraphService(db, user).neighborhood(
        document_id,
        depth=depth,
        max_nodes=max_nodes,
        max_edges=max_edges,
        relation_types=types,
        min_score=min_score,
    )
    if out.get("code"):
        _err(out["code"], "Узел недоступен")
    return out


@router.get("/documents/{document_id}/relations")
def document_relations(
    document_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    out = GraphService(db, user).document_relations(document_id, limit=limit, offset=offset)
    if out.get("code"):
        _err(out["code"], "Документ недоступен")
    return out


@router.get("/edges/{edge_id}")
def get_edge(
    edge_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    out = GraphService(db, user).get_edge(edge_id)
    if not out:
        _err("GRAPH_EDGE_NOT_FOUND_OR_FORBIDDEN", "Связь недоступна")
    return out


@router.get("/edges/{edge_id}/evidence")
def get_evidence(
    edge_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    out = GraphService(db, user).get_evidence(edge_id)
    if not out:
        _err("GRAPH_EDGE_NOT_FOUND_OR_FORBIDDEN", "Доказательства недоступны")
    return out


class ReviewIn(BaseModel):
    action: Literal["APPROVE", "REJECT", "REQUEST_CHANGES"]
    reason: str | None = Field(default=None, max_length=1000)
    expected_version: int | None = None


@router.post("/edges/{edge_id}/review")
def review_edge(
    edge_id: uuid.UUID,
    body: ReviewIn,
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    out = GraphService(db, user).review_edge(
        edge_id,
        action=body.action,
        reason=body.reason,
        expected_version=body.expected_version,
    )
    code = out.get("code")
    if code == "GRAPH_REVIEW_CONFLICT":
        _err(code, "Данные изменились", status=409)
    if code == "GRAPH_FORBIDDEN":
        _err(code, "Недостаточно прав", status=403)
    if code:
        _err(code, "Связь недоступна")
    return out


class ManualEdgeIn(BaseModel):
    source_document_id: uuid.UUID
    target_document_id: uuid.UUID
    relation_type: str = Field(default="MANUAL_LINK", max_length=64)
    directed: bool = True
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/edges/manual")
def manual_edge(
    body: ManualEdgeIn,
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    out = GraphService(db, user).create_manual_edge(
        source_document_id=body.source_document_id,
        target_document_id=body.target_document_id,
        relation_type=body.relation_type,
        directed=body.directed,
        reason=body.reason,
    )
    if out.get("code"):
        status = 403 if out["code"] == "GRAPH_FORBIDDEN" else 404
        _err(out["code"], "Не удалось создать связь", status=status)
    return out


class JobIn(BaseModel):
    mode: Literal["analyze_new_version", "rebuild_doc_graph", "rebuild_collection_graph"]
    document_id: uuid.UUID | None = None
    dry_run: bool = False


@router.post("/jobs")
def create_job(
    body: JobIn,
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    if body.mode in ("analyze_new_version", "rebuild_doc_graph") and not body.document_id:
        _err("GRAPH_VALIDATION_ERROR", "document_id required", status=400)
    if body.document_id and not acl.can_read_document(db, user, body.document_id):
        _err("GRAPH_NODE_NOT_FOUND_OR_FORBIDDEN", "Документ недоступен")
    if body.dry_run:
        from apps.api.models import Document

        if body.mode == "rebuild_collection_graph":
            n = (
                db.query(Document)
                .filter(
                    Document.tenant_id == user.tenant_id,
                    Document.deleted_at.is_(None),
                    Document.status != "deleted",
                    Document.active_version_id.is_not(None),
                )
                .count()
            )
            return {"dry_run": True, "documents": min(n, 500), "mode": body.mode}
        return {"dry_run": True, "documents": 1, "mode": body.mode}

    ver_id = None
    if body.document_id:
        doc = acl.can_read_document(db, user, body.document_id)
        ver_id = doc.active_version_id if doc else None
    job = enqueue_analyze(
        db,
        tenant_id=user.tenant_id,
        mode=body.mode,
        document_id=body.document_id,
        source_version_id=ver_id,
        created_by=user.id,
    )
    # For small single-doc jobs, process synchronously so UI sees edges immediately
    if body.mode in ("analyze_new_version", "rebuild_doc_graph"):
        process_job(db, job.id)
        db.refresh(job)
    return {
        "id": str(job.id),
        "state": job.state,
        "mode": job.mode,
        "progress": job.progress,
        "processed": job.processed,
        "total": job.total,
        "safe_error": job.safe_error,
    }


@router.get("/jobs/{job_id}")
def get_job(
    job_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    job = db.get(GraphBuildJob, job_id)
    if not job or job.tenant_id != user.tenant_id:
        _err("GRAPH_NODE_NOT_FOUND_OR_FORBIDDEN", "Задача недоступна")
    if user.role != "admin" and job.created_by != user.id:
        _err("GRAPH_NODE_NOT_FOUND_OR_FORBIDDEN", "Задача недоступна")
    return {
        "id": str(job.id),
        "state": job.state,
        "mode": job.mode,
        "progress": job.progress,
        "processed": job.processed,
        "total": job.total,
        "stage": job.stage,
        "error_code": job.error_code,
        "safe_error": job.safe_error,
    }


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    job = db.get(GraphBuildJob, job_id)
    if not job or job.tenant_id != user.tenant_id:
        _err("GRAPH_NODE_NOT_FOUND_OR_FORBIDDEN", "Задача недоступна")
    if job.state in ("SUCCEEDED", "FAILED", "CANCELLED"):
        return {"id": str(job.id), "state": job.state}
    job.state = "CANCELLED"
    db.commit()
    return {"id": str(job.id), "state": job.state}


@router.get("/export")
def export_graph(
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    format: Literal["json"] = "json",
    limit: int = Query(default=200, ge=1, le=500),
):
    from apps.api.services.graph.settings import get_graph_limits

    if not get_graph_limits().export_enabled and user.role != "admin":
        _err("GRAPH_FORBIDDEN", "Экспорт отключён", status=403)
    svc = GraphService(db, user)
    nodes = svc.nodes(limit=limit)
    ids = [uuid.UUID(n["document_id"]) for n in nodes["nodes"]]
    edges = svc.edges_for_nodes(ids, limit=get_graph_limits().hard_max_edges)
    return {
        "format": format,
        "nodes": nodes["nodes"],
        "edges": edges["edges"],
        "truncated": nodes.get("truncated") or edges.get("truncated"),
        "note": "Только видимые текущему пользователю узлы/рёбра",
    }


# --- Connectivity index (lexical/filename; no Qdrant; separate from Graph Explorer edges) ---


@router.post("/index/rebuild")
def rebuild_connectivity_index(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    sync: bool = Query(default=False),
    mode: str | None = Query(default=None),
):
    """Rebuild connectivity index in background (single-flight).

    Set ``sync=true`` only for tests/admin debugging — blocks until done.
    """
    from apps.api.services import graph_index as gidx
    from apps.api.services import graph_index_cache as gcache

    if sync:
        payload = gidx.build_index_for_tenant(db, user.tenant_id, force=True, mode=mode or "full")
        return {
            "status": "ok",
            "fingerprint": payload.get("fingerprint"),
            "built_ms": payload.get("built_ms"),
            "stats": payload.get("stats") or {},
            "build_mode": (payload.get("stats") or {}).get("build_mode"),
        }
    started = gidx.schedule_rebuild(user.tenant_id, force=True, mode=mode)
    st = gcache.read_build_status(user.tenant_id) or {}
    return {**started, "build": st}


@router.get("/index/stats")
def connectivity_index_stats(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from apps.api.services import graph_index as gidx
    from apps.api.services import graph_index_cache as gcache

    build = gcache.read_build_status(user.tenant_id) or {}
    if gcache.is_build_running(user.tenant_id) or build.get("status") == "running":
        return {
            "status": "building",
            "build": build,
        }
    cached = gidx.load_cached_index(db, user.tenant_id)
    if not cached:
        return {"status": "missing", "build": build}
    stats = cached.get("stats") or {}
    return {
        "status": "ok",
        "fingerprint": cached.get("fingerprint"),
        "built_at": cached.get("built_at"),
        "built_ms": cached.get("built_ms"),
        "params_hash": cached.get("params_hash"),
        "build_mode": stats.get("build_mode"),
        "lifecycle": stats.get("lifecycle"),
        "layer_ms": stats.get("layer_ms"),
        "stats": stats,
        "build": build,
    }


@router.get("/index/document/{document_id}")
def connectivity_index_document(
    document_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
):
    from apps.api.services import graph_index as gidx

    doc = db.get(Document, document_id)
    if not doc or doc.tenant_id != user.tenant_id or doc.deleted_at is not None or doc.status == "deleted":
        _err("GRAPH_NODE_NOT_FOUND_OR_FORBIDDEN", "Документ недоступен")

    cached = gidx.load_cached_index(db, user.tenant_id)
    if not cached:
        return {"status": "missing", "document_id": str(document_id)}
    out = gidx.neighbors_for_document(cached, str(document_id), limit=limit)
    out["status"] = "ok"
    return out
