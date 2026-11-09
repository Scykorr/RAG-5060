from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from apps.api.auth import AuthUser, require_admin
from apps.api.db import get_db
from apps.api.models import (
    AppSetting,
    ChatMessage,
    ChatSession,
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    ModelImportJob,
    OutboxEvent,
)
from apps.api.services import folder_import as folder_svc
from apps.api.services import ollama as ollama_svc
from apps.api.services.storage import cleanup_scratch, disk_stats, storage_summary
from apps.api.settings import get_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _http_ok(url: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get(url)
            return {"ok": r.status_code < 500, "status_code": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


@router.get("/system")
def system(
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    settings = get_settings()
    active = db.get(AppSetting, "active_model")
    waiting_jobs = (
        db.query(func.count(IngestionJob.id))
        .filter(IngestionJob.stage.in_(["queued", "parsing", "chunking", "embedding", "indexing"]))
        .scalar()
        or 0
    )
    outbox_pending = (
        db.query(func.count(OutboxEvent.id)).filter(OutboxEvent.published_at.is_(None)).scalar() or 0
    )
    from sqlalchemy import text

    services = {
        "valkey": _probe_valkey(settings.valkey_url),
        "qdrant": _http_ok(f"{settings.qdrant_url.rstrip('/')}/readyz"),
        "ollama": ollama_svc.health(),
        "api": {"ok": True},
    }
    try:
        db.execute(text("SELECT 1"))
        services["postgres"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        services["postgres"] = {"ok": False, "error": type(exc).__name__}

    gpu = {"note": "host nvidia-smi via launcher; container sees GPU through ollama"}
    ps = ollama_svc.ps()
    if ps.get("ok") and ps.get("data"):
        models = (ps["data"] or {}).get("models") or []
        if models:
            m0 = models[0]
            gpu = {
                "vram_used_mb": None,
                "vram_total_mb": None,
                "loaded": m0.get("name") or m0.get("model"),
                "raw": m0,
            }

    bad = {k: v for k, v in services.items() if not v.get("ok")}
    quality = {}
    try:
        from apps.api.services import quality_state as qstate

        # system endpoint has no user; use first tenant via active docs or skip
        from apps.api.models import Tenant

        t = db.query(Tenant).order_by(Tenant.created_at).first()
        if t:
            quality = qstate.get_level(db, t.id)
    except Exception as exc:  # noqa: BLE001
        quality = {"error": type(exc).__name__}
    return {
        "services": services,
        "services_ok": len(bad) == 0,
        "dependency_errors": bad,
        "disk": disk_stats(),
        "active_model": active.value if active else settings.ollama_model,
        "queue": {"ingestion": waiting_jobs, "outbox_pending": outbox_pending, "chat_waiting": 0},
        "gpu": gpu,
        "ollama_ps": ps,
        "compose_profile": settings.compose_profile,
        "offline": bool(settings.offline_mode),
        "llm_slot": 1,
        "quality": quality,
        "launcher_hints": {
            "start": "RAG5060 start   # or: docker compose --profile core up -d",
            "stop": "RAG5060 stop    # compose down; volumes kept",
            "status": "RAG5060 status",
            "ui": "http://127.0.0.1:18080",
            "data_root_env": "DATA_ROOT / RAG_DATA_ROOT (host) → mounted as /var/lib/rag",
        },
    }


@router.get("/logs")
def safe_logs(
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 40,
):
    """Safe recent errors from jobs — no secrets, no raw container dumps."""
    limit = max(1, min(100, limit))
    lines: list[dict[str, Any]] = []
    for j in (
        db.query(IngestionJob)
        .filter(IngestionJob.safe_error.is_not(None))
        .order_by(IngestionJob.started_at.desc().nullslast())
        .limit(limit)
        .all()
    ):
        lines.append(
            {
                "ts": j.started_at.isoformat() if j.started_at else None,
                "source": "ingestion",
                "ref": str(j.id),
                "level": "error" if j.stage == "failed" else "warn",
                "message": (j.safe_error or "")[:400],
            }
        )
    for j in (
        db.query(ModelImportJob)
        .filter(ModelImportJob.detail.is_not(None))
        .order_by(ModelImportJob.created_at.desc())
        .limit(limit)
        .all()
    ):
        if j.status not in ("failed", "cancelled") and not (j.detail or "").startswith("http"):
            # skip raw URLs as noise; keep failures / cancel notes
            if j.status == "done":
                continue
        lines.append(
            {
                "ts": (j.finished_at or j.created_at).isoformat() if (j.finished_at or j.created_at) else None,
                "source": "model",
                "ref": str(j.id),
                "level": "error" if j.status == "failed" else "info",
                "message": f"{j.name} [{j.source}/{j.status}] {(j.detail or '')[:300]}",
            }
        )
    lines.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return {"items": lines[:limit], "note": "Sanitized job messages only; use launcher/docker logs for full stack."}


def _probe_valkey(url: str) -> dict[str, Any]:
    try:
        import redis

        client = redis.from_url(url, socket_connect_timeout=2)
        ok = bool(client.ping())
        client.close()
        return {"ok": ok}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


@router.get("/storage")
def storage(_: Annotated[AuthUser, Depends(require_admin)]):
    return storage_summary()


@router.get("/dispatcher")
def dispatcher(
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Live RAG task dispatcher: services, queues, jobs, corpus, model, disk."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    settings = get_settings()
    now = datetime.now(timezone.utc).isoformat()
    active = db.get(AppSetting, "active_model")

    services = {
        "postgres": {"ok": False},
        "valkey": _probe_valkey(settings.valkey_url),
        "qdrant": _http_ok(f"{settings.qdrant_url.rstrip('/')}/readyz"),
        "ollama": ollama_svc.health(),
        "api": {"ok": True},
    }
    try:
        db.execute(text("SELECT 1"))
        services["postgres"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        services["postgres"] = {"ok": False, "error": type(exc).__name__}

    stage_rows = (
        db.query(IngestionJob.stage, func.count(IngestionJob.id)).group_by(IngestionJob.stage).all()
    )
    jobs_by_stage = {stage: int(cnt) for stage, cnt in stage_rows}
    active_stages = ("queued", "parsing", "chunking", "embedding", "indexing")
    ingestion_active = sum(jobs_by_stage.get(s, 0) for s in active_stages)

    recent_jobs = (
        db.query(IngestionJob).order_by(IngestionJob.started_at.desc().nullslast()).limit(12).all()
    )
    model_jobs = (
        db.query(ModelImportJob).order_by(ModelImportJob.created_at.desc()).limit(8).all()
    )

    docs_active = (
        db.query(func.count(Document.id))
        .filter(Document.deleted_at.is_(None), Document.status != "deleted")
        .scalar()
        or 0
    )
    docs_deleted = (
        db.query(func.count(Document.id))
        .filter((Document.deleted_at.is_not(None)) | (Document.status == "deleted"))
        .scalar()
        or 0
    )
    chunks_n = db.query(func.count(Chunk.id)).scalar() or 0
    chats_n = db.query(func.count(ChatSession.id)).scalar() or 0
    outbox_pending = (
        db.query(func.count(OutboxEvent.id)).filter(OutboxEvent.published_at.is_(None)).scalar() or 0
    )

    ps = ollama_svc.ps()
    loaded = []
    if ps.get("ok") and ps.get("data"):
        for m in (ps["data"] or {}).get("models") or []:
            loaded.append(
                {
                    "name": m.get("name") or m.get("model"),
                    "size_vram": m.get("size_vram"),
                    "expires_at": m.get("expires_at"),
                }
            )

    folder_state = folder_svc.import_state()
    disk = disk_stats()
    storage = storage_summary()

    return {
        "generated_at": now,
        "offline": True,
        "services": services,
        "services_ok": all(bool(v.get("ok")) for v in services.values()),
        "active_model": active.value if active else settings.ollama_model,
        "ollama_loaded": loaded,
        "disk": disk,
        "storage": {
            "originals_bytes": storage.get("originals_bytes", 0),
            "extracted_bytes": storage.get("extracted_bytes", 0),
            "imports_bytes": storage.get("imports_bytes", 0),
            "file_count": len(storage.get("files") or []),
        },
        "corpus": {
            "documents_active": int(docs_active),
            "documents_deleted": int(docs_deleted),
            "chunks": int(chunks_n),
            "chat_sessions": int(chats_n),
        },
        "queues": {
            "ingestion_active": int(ingestion_active),
            "jobs_by_stage": jobs_by_stage,
            "outbox_pending": int(outbox_pending),
            "folder_import_running": bool(folder_state.get("running")),
            "llm_slot": 1,
            "llm_note": "MVP: одна генерация одновременно (OLLAMA_NUM_PARALLEL=1)",
        },
        "folder_import": folder_state,
        "recent_ingestion_jobs": [
            {
                "id": str(j.id),
                "document_id": str(j.document_id),
                "stage": j.stage,
                "progress": j.progress,
                "attempts": j.attempts,
                "safe_error": j.safe_error,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            }
            for j in recent_jobs
        ],
        "recent_model_jobs": [
            {
                "id": str(j.id),
                "name": j.name,
                "source": j.source,
                "status": j.status,
                "progress": j.progress,
                "detail": j.detail,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            }
            for j in model_jobs
        ],
        "inbox": {
            "path": str(folder_svc.inbox_root()),
            "host_hint": settings.import_inbox_host_hint,
        },
    }


class CleanupIn(BaseModel):
    confirm: bool = False
    tombstoned: bool = False


class DataResetIn(BaseModel):
    """Dangerous admin reset — requires confirm=true."""
    confirm: bool = False
    chats: bool = True
    documents: bool = True
    scratch: bool = True
    qdrant: bool = True
    clear_retrieval_prefix: bool = True


class ChatEvalConfigIn(BaseModel):
    chat_prompt_mode: str | None = None  # general|extractive|cloze
    retrieve_limit: int | None = None
    retrieve_scan: int | None = None
    retrieval_filename_prefix: str | None = None


def _chat_eval_get(db: Session) -> dict[str, Any]:
    def _v(key: str, default: str) -> str:
        row = db.get(AppSetting, key)
        return row.value if row else default

    return {
        "chat_prompt_mode": _v("chat_prompt_mode", "general"),
        "retrieve_limit": int(_v("retrieve_limit", "8")),
        "retrieve_scan": int(_v("retrieve_scan", "600")),
        "retrieval_filename_prefix": _v("retrieval_filename_prefix", ""),
    }


def _chat_eval_set(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


@router.get("/corpus-packs")
def get_corpus_packs(
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Local category registry: pack_id → prefix (auto-filled on import)."""
    from apps.api.services import corpus_packs

    packs = corpus_packs.list_packs(db)
    cfg = _chat_eval_get(db)
    return {
        **packs,
        "retrieval_filename_prefix": cfg.get("retrieval_filename_prefix", ""),
        "hint": "Префикс выбирается автоматически при импорте папки (reuse или new).",
    }


@router.get("/chat-eval-config")
def get_chat_eval_config(
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    return _chat_eval_get(db)


@router.post("/chat-eval-config")
def set_chat_eval_config(
    body: ChatEvalConfigIn,
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    if body.chat_prompt_mode is not None:
        mode = body.chat_prompt_mode.strip().lower()
        if mode not in {"general", "extractive", "cloze"}:
            mode = "general"
        _chat_eval_set(db, "chat_prompt_mode", mode)
    if body.retrieve_limit is not None:
        _chat_eval_set(db, "retrieve_limit", str(max(1, min(32, int(body.retrieve_limit)))))
    if body.retrieve_scan is not None:
        _chat_eval_set(db, "retrieve_scan", str(max(50, min(5000, int(body.retrieve_scan)))))
    if body.retrieval_filename_prefix is not None:
        _chat_eval_set(db, "retrieval_filename_prefix", body.retrieval_filename_prefix[:120])
    db.commit()
    return _chat_eval_get(db)


@router.post("/storage/cleanup")
def storage_cleanup(
    body: CleanupIn,
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    if not body.confirm:
        return {"status": "noop", "removed": 0, "message": "Set confirm=true to run cleanup"}
    if body.tombstoned:
        removed = 0
        docs = db.query(Document).filter(Document.status == "deleted").all()
        for doc in docs:
            for ver in db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id):
                if ver.storage_key:
                    from pathlib import Path

                    p = Path(ver.storage_key)
                    if p.is_file():
                        p.unlink(missing_ok=True)
                        removed += 1
        return {"status": "ok", "removed": removed, "mode": "tombstoned"}
    return cleanup_scratch()


def _wipe_qdrant_collection() -> dict[str, Any]:
    settings = get_settings()
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=settings.qdrant_url, timeout=10)
        names = {c.name for c in client.get_collections().collections}
        if settings.qdrant_collection in names:
            client.delete_collection(settings.qdrant_collection)
            return {"ok": True, "deleted": settings.qdrant_collection}
        return {"ok": True, "deleted": None, "note": "collection absent"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.post("/data/purge-chats")
def purge_chats(
    body: CleanupIn,
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Delete all chat sessions and messages (admin)."""
    if not body.confirm:
        return {"status": "noop", "removed": 0, "message": "Set confirm=true"}
    msg_n = db.query(ChatMessage).delete()
    n = db.query(ChatSession).delete()
    db.commit()
    return {"status": "ok", "removed_sessions": int(n or 0), "removed_messages": int(msg_n or 0)}


@router.post("/data/reset-rag")
def reset_rag(
    body: DataResetIn,
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Clear chats / soft-delete documents / scratch / Qdrant so a new corpus starts clean.

    Never touches filesystem knowledge: docs/SKILLS.md, docs/ERRORS.md, docs/ABILITIES.md
    (and Обучение/docs mirrors). Those stay and guide the next corpus.
    """
    if not body.confirm:
        return {"status": "noop", "message": "Set confirm=true to reset RAG data"}

    from datetime import datetime, timezone
    from pathlib import Path

    out: dict[str, Any] = {
        "status": "ok",
        "preserved_knowledge_docs": [
            "docs/SKILLS.md",
            "docs/ERRORS.md",
            "docs/ABILITIES.md",
            "Обучение/docs/*",
        ],
    }

    if body.chats:
        out["messages_removed"] = int(db.query(ChatMessage).delete() or 0)
        out["chats_removed"] = int(db.query(ChatSession).delete() or 0)

    docs_tombstoned = 0
    chunks_removed = 0
    files_removed = 0
    if body.documents:
        docs = db.query(Document).filter(Document.deleted_at.is_(None)).all()
        now = datetime.now(timezone.utc)
        for doc in docs:
            # Drop searchable chunks immediately so retrieval ignores them.
            chunks_removed += (
                db.query(Chunk).filter(Chunk.document_id == doc.id).delete(synchronize_session=False) or 0
            )
            for ver in db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id):
                if ver.storage_key:
                    p = Path(ver.storage_key)
                    if p.is_file():
                        p.unlink(missing_ok=True)
                        files_removed += 1
            doc.deleted_at = now
            doc.status = "deleted"
            doc.active_version_id = None
            docs_tombstoned += 1
        out["documents_tombstoned"] = docs_tombstoned
        out["chunks_removed"] = chunks_removed
        out["original_files_removed"] = files_removed

    if body.clear_retrieval_prefix:
        _chat_eval_set(db, "retrieval_filename_prefix", "")
        _chat_eval_set(db, "chat_prompt_mode", "extractive")
        out["retrieval_filename_prefix"] = ""
        out["chat_prompt_mode"] = "extractive"

    db.commit()

    if body.scratch:
        out["scratch"] = cleanup_scratch()
    if body.qdrant:
        out["qdrant"] = _wipe_qdrant_collection()

    return out
