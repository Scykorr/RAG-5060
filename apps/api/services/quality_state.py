"""Quality level / fingerprint / auto-improve flags (local app_settings)."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from apps.api.models import AppSetting, Document, DocumentVersion


def _get(db: Session, key: str, default: str = "") -> str:
    row = db.get(AppSetting, key)
    return row.value if row else default


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def auto_improve_enabled(db: Session) -> bool:
    return _get(db, "quality_auto_improve", "1") not in ("0", "false", "False", "no")


def set_auto_improve(db: Session, enabled: bool) -> None:
    _set(db, "quality_auto_improve", "1" if enabled else "0")


def corpus_fingerprint(db: Session, tenant_id: UUID) -> str:
    rows = (
        db.query(Document.id, Document.active_version_id, DocumentVersion.sha256)
        .outerjoin(DocumentVersion, DocumentVersion.id == Document.active_version_id)
        .filter(
            Document.tenant_id == tenant_id,
            Document.deleted_at.is_(None),
            Document.status != "deleted",
        )
        .order_by(Document.id)
        .all()
    )
    n = (
        db.query(func.count(Document.id))
        .filter(
            Document.tenant_id == tenant_id,
            Document.deleted_at.is_(None),
            Document.status != "deleted",
        )
        .scalar()
        or 0
    )
    parts = [f"{n}"]
    for doc_id, ver_id, sha in rows:
        parts.append(f"{doc_id}:{ver_id or ''}:{sha or ''}")
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def mark_stale(db: Session, *, reason: str = "corpus_changed") -> None:
    _set(db, "quality_status", "stale")
    _set(db, "quality_stale_reason", reason[:200])


def set_level(
    db: Session,
    *,
    status: str,
    score: float | None,
    level: str | None,
    threshold: float,
    fingerprint: str | None,
    run_id: str | None = None,
) -> None:
    _set(db, "quality_status", status)
    if score is not None:
        _set(db, "quality_score", f"{score:.4f}")
    if level:
        _set(db, "quality_level", level)
    _set(db, "quality_threshold", f"{threshold:.4f}")
    if fingerprint:
        _set(db, "quality_corpus_fingerprint", fingerprint)
    if run_id:
        _set(db, "quality_last_run_id", run_id)


def get_level(db: Session, tenant_id: UUID) -> dict[str, Any]:
    fp = corpus_fingerprint(db, tenant_id)
    last_fp = _get(db, "quality_corpus_fingerprint", "")
    status = _get(db, "quality_status", "unknown")
    if last_fp and fp != last_fp and status not in ("running", "queued"):
        status = "stale"
    score_s = _get(db, "quality_score", "")
    try:
        score = float(score_s) if score_s else None
    except ValueError:
        score = None
    thr_s = _get(db, "quality_threshold", "0.55")
    try:
        threshold = float(thr_s)
    except ValueError:
        threshold = 0.55
    docs = (
        db.query(func.count(Document.id))
        .filter(
            Document.tenant_id == tenant_id,
            Document.deleted_at.is_(None),
            Document.status != "deleted",
        )
        .scalar()
        or 0
    )
    return {
        "status": status,
        "level": _get(db, "quality_level", "") or None,
        "score": score,
        "threshold": threshold,
        "corpus_fingerprint": fp,
        "last_run_fingerprint": last_fp or None,
        "stale": status == "stale" or (bool(last_fp) and fp != last_fp),
        "stale_reason": _get(db, "quality_stale_reason", "") or None,
        "last_run_id": _get(db, "quality_last_run_id", "") or None,
        "auto_improve": auto_improve_enabled(db),
        "documents_active": int(docs),
        "offline": True,
        "detail": _get(db, "quality_detail", "") or None,
    }


def snapshot_chat_config(db: Session) -> dict[str, Any]:
    return {
        "chat_prompt_mode": _get(db, "chat_prompt_mode", "general"),
        "retrieve_limit": _get(db, "retrieve_limit", "8"),
        "retrieve_scan": _get(db, "retrieve_scan", "600"),
        "retrieval_filename_prefix": _get(db, "retrieval_filename_prefix", ""),
    }


def apply_recipe(db: Session, attempt: int) -> dict[str, Any]:
    """Fixed local recipes (no network) — mirrors Обучение apply_improvement."""
    if attempt <= 1:
        cfg = {
            "chat_prompt_mode": "extractive",
            "retrieve_limit": "8",
            "retrieve_scan": "600",
        }
        note = "baseline extractive"
    elif attempt == 2:
        cfg = {
            "chat_prompt_mode": "extractive",
            "retrieve_limit": "10",
            "retrieve_scan": "800",
        }
        note = "wider scan"
    elif attempt == 3:
        cfg = {
            "chat_prompt_mode": "extractive",
            "retrieve_limit": "12",
            "retrieve_scan": "1200",
        }
        note = "more chunks"
    elif attempt == 4:
        cfg = {
            "chat_prompt_mode": "extractive",
            "retrieve_limit": "16",
            "retrieve_scan": "2000",
        }
        note = "aggressive retrieval"
    elif attempt == 5:
        cfg = {
            "chat_prompt_mode": "general",
            "retrieve_limit": "12",
            "retrieve_scan": "1200",
        }
        note = "general prompt"
    else:
        cfg = {
            "chat_prompt_mode": "extractive",
            "retrieve_limit": "20",
            "retrieve_scan": "2500",
        }
        note = "max retrieval"
    for k, v in cfg.items():
        _set(db, k, str(v))
    # keep existing prefix unless empty
    if not _get(db, "retrieval_filename_prefix", ""):
        pass
    db.commit()
    return {"note": note, "config": {**snapshot_chat_config(db)}}
