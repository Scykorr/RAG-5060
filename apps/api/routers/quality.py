from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import AuthUser, require_admin
from apps.api.db import get_db
from apps.api.models import EvalItem, EvalSet, QualityRun
from apps.api.services import quality_loop as qloop
from apps.api.services import quality_state as qstate
from apps.api.services import quality_synth as synth
from apps.api.settings import get_settings

router = APIRouter(prefix="/api/admin/quality", tags=["quality"])


class ImportEvalIn(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    replace: bool = False


class StartRunIn(BaseModel):
    threshold: float = 0.55
    max_attempts: int = 8
    auto_synth_if_empty: bool = True


class AutoImproveIn(BaseModel):
    enabled: bool = True


class SynthIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


@router.get("/level")
def quality_level(
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    return qstate.get_level(db, user.tenant_id)


@router.post("/auto-improve")
def set_auto_improve(
    body: AutoImproveIn,
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    qstate.set_auto_improve(db, body.enabled)
    db.commit()
    return {"auto_improve": body.enabled, "offline": True}


@router.get("/eval-set")
def get_eval_set(
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    eset = (
        db.query(EvalSet)
        .filter(EvalSet.tenant_id == user.tenant_id, EvalSet.name == "default")
        .order_by(EvalSet.created_at.desc())
        .first()
    )
    if not eset:
        return {"eval_set_id": None, "items": [], "total": 0}
    items = (
        db.query(EvalItem)
        .filter(EvalItem.eval_set_id == eset.id)
        .order_by(EvalItem.created_at.desc())
        .limit(200)
        .all()
    )
    return {
        "eval_set_id": str(eset.id),
        "total": db.query(EvalItem).filter(EvalItem.eval_set_id == eset.id).count(),
        "items": [
            {
                "id": str(i.id),
                "question": i.question,
                "gold_answers": i.gold_answers,
                "document_id": str(i.document_id) if i.document_id else None,
                "split": i.split,
                "method": i.method,
            }
            for i in items
        ],
    }


@router.post("/eval-set/import")
def import_eval(
    body: ImportEvalIn,
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    if not body.items:
        raise HTTPException(status_code=400, detail="items required")
    return qloop.import_eval_items(db, user.tenant_id, body.items, replace=body.replace)


@router.post("/eval-set/synthesize")
def synthesize(
    body: SynthIn,
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    # Local Ollama only — never pull / never cloud
    settings = get_settings()
    host = settings.ollama_host.rstrip("/")
    if host.startswith("https://") and "localhost" not in host and "127.0.0.1" not in host:
        # allow docker service name http://ollama:11434
        if "ollama" not in host:
            raise HTTPException(status_code=400, detail="Synth only allowed against local Ollama")
    return synth.synthesize_for_new_docs(db, user.tenant_id, limit=body.limit)


@router.post("/runs")
def start_run(
    body: StartRunIn,
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    run = qloop.start_quality_run(
        db,
        tenant_id=user.tenant_id,
        threshold=max(0.1, min(1.0, body.threshold)),
        max_attempts=max(1, min(12, body.max_attempts)),
        auto_synth_if_empty=body.auto_synth_if_empty,
    )
    return {
        "run_id": str(run.id),
        "status": run.status,
        "threshold": run.threshold,
        "corpus_fingerprint": run.corpus_fingerprint,
        "offline": True,
    }


@router.get("/runs")
def list_runs(
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 20,
):
    limit = max(1, min(50, limit))
    rows = (
        db.query(QualityRun)
        .filter(QualityRun.tenant_id == user.tenant_id)
        .order_by(QualityRun.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": str(r.id),
                "status": r.status,
                "attempt": r.attempt,
                "score": r.score,
                "threshold": r.threshold,
                "level": r.level,
                "detail": r.detail,
                "corpus_fingerprint": r.corpus_fingerprint,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]
    }


@router.get("/runs/{run_id}")
def get_run(
    run_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    run = db.get(QualityRun, run_id)
    if not run or run.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "id": str(run.id),
        "status": run.status,
        "attempt": run.attempt,
        "max_attempts": run.max_attempts,
        "score": run.score,
        "threshold": run.threshold,
        "level": run.level,
        "detail": run.detail,
        "config_snapshot": run.config_snapshot,
        "corpus_fingerprint": run.corpus_fingerprint,
        "artifact_dir": run.artifact_dir,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "offline": True,
    }


@router.post("/runs/{run_id}/cancel")
def cancel(
    run_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    run = db.get(QualityRun, run_id)
    if not run or run.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Not found")
    qloop.cancel_run(db, run_id)
    db.refresh(run)
    return {"id": str(run.id), "status": run.status}
