"""Offline quality loop: eval → recipes → re-eval until threshold or max attempts."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from apps.api.db import SessionLocal
from apps.api.models import AppSetting, EvalItem, EvalSet, QualityRun
from apps.api.services import ollama as ollama_svc
from apps.api.services import quality_state as qstate
from apps.api.services.quality_scoring import contain_hit, level_from_score
from apps.api.services.storage import data_root

_LOCK = threading.Lock()
_RUNNING: set[uuid.UUID] = set()


def _artifact_dir(run_id: uuid.UUID) -> Path:
    # Prefer repo artifacts if mounted; else under data_root
    candidates = [
        Path("/mnt/d/RAG 5060/artifacts/quality") / str(run_id),
        data_root() / "quality" / str(run_id),
    ]
    for p in candidates:
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:  # noqa: BLE001
            continue
    p = Path("/tmp") / "rag-quality" / str(run_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _answer_once(db: Session, tenant_id: uuid.UUID, question: str) -> dict[str, Any]:
    """Call local retrieve+generate (no HTTP egress)."""
    from apps.api.routers import chat as chat_mod

    active = db.get(AppSetting, "active_model")
    model = active.value if active else "qwen3:8b"
    cfg = chat_mod._eval_cfg(db)
    chunks = chat_mod._retrieve(
        db,
        tenant_id,
        question,
        limit=cfg["limit"],
        scan=cfg["scan"],
        filename_prefix=cfg["prefix"],
    )
    if not chunks:
        return {"answer": "не найдено в документах", "outcome": "not_found", "citations": []}
    numbered = [f"[{i}] {ch.text[:1200]}" for i, ch in enumerate(chunks, start=1)]
    citations = [
        {
            "document_id": str(ch.document_id),
            "chunk_id": str(ch.id),
            "page": ch.page_start,
        }
        for ch in chunks
    ]
    if cfg["mode"] == "cloze":
        fb = chat_mod._cloze_fallback(question, chunks)
        if fb:
            return {"answer": fb, "outcome": "answer", "citations": citations}
    prompt = chat_mod._prompt_for_mode(cfg["mode"], question, numbered, candidates=None)
    try:
        answer = ollama_svc.generate(prompt, model, think=False)
    except Exception as exc:  # noqa: BLE001
        return {"answer": "", "outcome": "error", "citations": [], "error": str(exc)[:200]}
    return {"answer": answer or "", "outcome": "answer", "citations": citations, "model": model}


def _ensure_eval_set(db: Session, tenant_id: uuid.UUID) -> EvalSet:
    row = (
        db.query(EvalSet)
        .filter(EvalSet.tenant_id == tenant_id, EvalSet.name == "default")
        .order_by(EvalSet.created_at.desc())
        .first()
    )
    if row:
        return row
    row = EvalSet(tenant_id=tenant_id, name="default")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def import_eval_items(
    db: Session,
    tenant_id: uuid.UUID,
    items: list[dict[str, Any]],
    *,
    replace: bool = False,
) -> dict[str, Any]:
    eset = _ensure_eval_set(db, tenant_id)
    if replace:
        db.query(EvalItem).filter(EvalItem.eval_set_id == eset.id).delete()
        db.commit()
    added = 0
    for it in items:
        q = (it.get("question") or "").strip()
        if not q:
            continue
        golds = it.get("gold_answers") or it.get("answers") or it.get("gold") or []
        if isinstance(golds, str):
            golds = [golds]
        golds = [str(g) for g in golds if str(g).strip()]
        if not golds:
            continue
        doc_id = it.get("document_id")
        split = it.get("split") or "calibration"
        if split not in ("calibration", "held_out"):
            split = "calibration"
        method = it.get("method") or "import"
        row = EvalItem(
            tenant_id=tenant_id,
            eval_set_id=eset.id,
            question=q,
            gold_answers=golds,
            document_id=uuid.UUID(str(doc_id)) if doc_id else None,
            split=split,
            method=method if method in ("import", "synthetic") else "import",
        )
        db.add(row)
        added += 1
    db.commit()
    total = db.query(EvalItem).filter(EvalItem.eval_set_id == eset.id).count()
    return {"eval_set_id": str(eset.id), "added": added, "total": total}


def evaluate_once(db: Session, tenant_id: uuid.UUID, items: list[EvalItem]) -> dict[str, Any]:
    # Quality runs score against the full active corpus (old∪new), not a pack prefix.
    saved_prefix = qstate._get(db, "retrieval_filename_prefix", "")
    qstate._set(db, "retrieval_filename_prefix", "")
    db.commit()
    results = []
    hits = 0
    try:
        for it in items:
            out = _answer_once(db, tenant_id, it.question)
            ok = contain_hit(out.get("answer") or "", list(it.gold_answers or []))
            if ok:
                hits += 1
            results.append(
                {
                    "id": str(it.id),
                    "question": it.question[:200],
                    "gold": it.gold_answers,
                    "answer": (out.get("answer") or "")[:400],
                    "hit": ok,
                    "outcome": out.get("outcome"),
                }
            )
    finally:
        qstate._set(db, "retrieval_filename_prefix", saved_prefix)
        db.commit()
    n = len(items) or 1
    score = hits / n if items else 0.0
    return {"score": score, "hits": hits, "total": len(items), "results": results}


def start_quality_run(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    threshold: float = 0.55,
    max_attempts: int = 8,
    auto_synth_if_empty: bool = True,
) -> QualityRun:
    # Supersede running
    running = (
        db.query(QualityRun)
        .filter(QualityRun.tenant_id == tenant_id, QualityRun.status.in_(["queued", "running"]))
        .all()
    )
    for r in running:
        r.status = "superseded"
        r.finished_at = datetime.now(timezone.utc)
        r.detail = "superseded by new run"
    eset = _ensure_eval_set(db, tenant_id)
    items_n = db.query(EvalItem).filter(EvalItem.eval_set_id == eset.id).count()
    fp = qstate.corpus_fingerprint(db, tenant_id)
    run = QualityRun(
        tenant_id=tenant_id,
        eval_set_id=eset.id,
        status="queued",
        threshold=threshold,
        max_attempts=max_attempts,
        corpus_fingerprint=fp,
        config_snapshot=qstate.snapshot_chat_config(db),
        detail="queued" + ("; will synth if empty" if items_n == 0 and auto_synth_if_empty else ""),
    )
    db.add(run)
    qstate.set_level(
        db,
        status="running",
        score=None,
        level=None,
        threshold=threshold,
        fingerprint=fp,
        run_id=None,
    )
    db.commit()
    db.refresh(run)

    rid = run.id

    def _worker():
        with _LOCK:
            if rid in _RUNNING:
                return
            _RUNNING.add(rid)
        s = SessionLocal()
        try:
            _run_loop(s, rid, auto_synth_if_empty=auto_synth_if_empty)
        finally:
            with _LOCK:
                _RUNNING.discard(rid)
            s.close()

    threading.Thread(target=_worker, daemon=True).start()
    return run


def _run_loop(db: Session, run_id: uuid.UUID, *, auto_synth_if_empty: bool) -> None:
    run = db.get(QualityRun, run_id)
    if not run or run.status == "superseded":
        return
    run.status = "running"
    db.commit()
    art = _artifact_dir(run_id)
    run.artifact_dir = str(art)
    db.commit()

    items = (
        db.query(EvalItem)
        .filter(EvalItem.eval_set_id == run.eval_set_id, EvalItem.split == "calibration")
        .all()
    )
    if not items:
        items = db.query(EvalItem).filter(EvalItem.eval_set_id == run.eval_set_id).all()
    if not items and auto_synth_if_empty:
        from apps.api.services import quality_synth as synth

        try:
            synth.synthesize_for_new_docs(db, run.tenant_id, limit=12)
            db.commit()
            items = db.query(EvalItem).filter(EvalItem.eval_set_id == run.eval_set_id).all()
        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.detail = f"synth failed: {exc}"[:500]
            run.finished_at = datetime.now(timezone.utc)
            qstate.set_level(
                db,
                status="failed",
                score=0.0,
                level="low",
                threshold=run.threshold,
                fingerprint=run.corpus_fingerprint,
                run_id=str(run.id),
            )
            db.commit()
            return

    if not items:
        run.status = "failed"
        run.detail = "no eval items"
        run.finished_at = datetime.now(timezone.utc)
        qstate.set_level(
            db,
            status="failed",
            score=0.0,
            level="low",
            threshold=run.threshold,
            fingerprint=run.corpus_fingerprint,
            run_id=str(run.id),
        )
        db.commit()
        return

    best_score = 0.0
    last_detail = ""
    for attempt in range(1, run.max_attempts + 1):
        run = db.get(QualityRun, run_id)
        if not run or run.status in ("cancelled", "superseded"):
            return
        run.attempt = attempt
        recipe = qstate.apply_recipe(db, attempt)
        run.config_snapshot = recipe.get("config")
        db.commit()

        ev = evaluate_once(db, run.tenant_id, items)
        score = float(ev["score"])
        best_score = max(best_score, score)
        (art / f"attempt{attempt}.json").write_text(
            json.dumps({"recipe": recipe, "eval": ev}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        run.score = score
        run.level = level_from_score(score)
        last_detail = f"attempt {attempt}: score={score:.3f} ({ev['hits']}/{ev['total']}) {recipe.get('note')}"
        run.detail = last_detail
        db.commit()

        if score >= run.threshold:
            run.status = "ok"
            run.finished_at = datetime.now(timezone.utc)
            fp = qstate.corpus_fingerprint(db, run.tenant_id)
            run.corpus_fingerprint = fp
            qstate.set_level(
                db,
                status="ok",
                score=score,
                level=run.level,
                threshold=run.threshold,
                fingerprint=fp,
                run_id=str(run.id),
            )
            db.commit()
            (art / "summary.json").write_text(
                json.dumps(
                    {"status": "ok", "score": score, "level": run.level, "attempts": attempt},
                    indent=2,
                ),
                encoding="utf-8",
            )
            return

    run = db.get(QualityRun, run_id)
    if not run or run.status in ("cancelled", "superseded"):
        return
    run.status = "failed"
    run.score = best_score
    run.level = level_from_score(best_score)
    run.detail = f"below threshold after {run.max_attempts} attempts; best={best_score:.3f}. {last_detail}"
    run.finished_at = datetime.now(timezone.utc)
    fp = qstate.corpus_fingerprint(db, run.tenant_id)
    run.corpus_fingerprint = fp
    qstate.set_level(
        db,
        status="failed",
        score=best_score,
        level=run.level,
        threshold=run.threshold,
        fingerprint=fp,
        run_id=str(run.id),
    )
    db.commit()
    (art / "summary.json").write_text(
        json.dumps(
            {"status": "failed", "score": best_score, "level": run.level, "detail": run.detail},
            indent=2,
        ),
        encoding="utf-8",
    )


def cancel_run(db: Session, run_id: uuid.UUID) -> QualityRun | None:
    run = db.get(QualityRun, run_id)
    if not run:
        return None
    if run.status in ("ok", "failed", "cancelled", "superseded"):
        return run
    run.status = "cancelled"
    run.finished_at = datetime.now(timezone.utc)
    run.detail = (run.detail or "") + " | cancelled"
    qstate.mark_stale(db, reason="run_cancelled")
    db.commit()
    return run
