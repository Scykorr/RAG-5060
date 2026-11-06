"""Synthesize eval items from local chunks via local Ollama only (no cloud)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from apps.api.models import Chunk, Document, EvalItem, EvalSet
from apps.api.services import ollama as ollama_svc
from apps.api.models import AppSetting


def _ensure_set(db: Session, tenant_id: uuid.UUID) -> EvalSet:
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


def _docs_without_eval(db: Session, tenant_id: uuid.UUID, limit: int) -> list[uuid.UUID]:
    have = {
        r[0]
        for r in db.query(EvalItem.document_id)
        .filter(EvalItem.tenant_id == tenant_id, EvalItem.document_id.is_not(None))
        .distinct()
        .all()
        if r[0]
    }
    docs = (
        db.query(Document.id)
        .filter(
            Document.tenant_id == tenant_id,
            Document.deleted_at.is_(None),
            Document.status != "deleted",
            Document.active_version_id.is_not(None),
        )
        .order_by(Document.created_at.desc())
        .limit(limit * 3)
        .all()
    )
    out = []
    for (did,) in docs:
        if did not in have:
            out.append(did)
        if len(out) >= limit:
            break
    return out


def _parse_qa(raw: str, passage: str) -> tuple[str, str] | None:
    text = (raw or "").strip()
    # Expect JSON {"question":"...","answer":"..."} or Q:/A: lines
    try:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            obj = json.loads(m.group(0))
            q = str(obj.get("question") or "").strip()
            a = str(obj.get("answer") or "").strip()
            if q and a and a.lower() in passage.lower():
                return q, a
            if q and a and len(a) <= 120:
                # require answer substring loosely
                if a[:20].lower() in passage.lower() or any(
                    tok in passage.lower() for tok in a.lower().split() if len(tok) > 4
                ):
                    return q, a
    except Exception:  # noqa: BLE001
        pass
    qm = re.search(r"(?:question|вопрос)\s*[:：]\s*(.+)", text, re.I)
    am = re.search(r"(?:answer|ответ)\s*[:：]\s*(.+)", text, re.I)
    if qm and am:
        q, a = qm.group(1).strip(), am.group(1).strip()
        if q and a:
            return q, a
    return None


def synthesize_for_new_docs(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    limit: int = 10,
    per_doc: int = 1,
) -> dict[str, Any]:
    """Create synthetic calibration items for docs that lack eval coverage."""
    eset = _ensure_set(db, tenant_id)
    active = db.get(AppSetting, "active_model")
    model = active.value if active else "qwen3:8b"
    doc_ids = _docs_without_eval(db, tenant_id, limit)
    if not doc_ids:
        # still ensure some items exist: sample recent chunks
        doc_ids = [
            r[0]
            for r in db.query(Document.id)
            .filter(
                Document.tenant_id == tenant_id,
                Document.deleted_at.is_(None),
                Document.status != "deleted",
            )
            .order_by(Document.created_at.desc())
            .limit(limit)
            .all()
        ]
    added = 0
    errors: list[str] = []
    for did in doc_ids:
        chunks = (
            db.query(Chunk)
            .filter(Chunk.tenant_id == tenant_id, Chunk.document_id == did)
            .order_by(Chunk.ordinal)
            .limit(3)
            .all()
        )
        if not chunks:
            continue
        passage = "\n".join(c.text for c in chunks)[:2500]
        prompt = (
            "Ты работаешь строго локально. По фрагменту документа составь ОДИН короткий фактический "
            "вопрос и короткий ответ ТОЛЬКО цитатой из фрагмента. "
            'Верни JSON: {"question":"...","answer":"..."}.\n\n'
            f"Фрагмент:\n{passage}"
        )
        try:
            raw = ollama_svc.generate(prompt, model, think=False)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{did}: {type(exc).__name__}")
            continue
        parsed = _parse_qa(raw or "", passage)
        if not parsed:
            # Fallback: first sentence as answer, generic question
            sent = re.split(r"(?<=[.!?])\s+", passage.strip())
            a = (sent[0] if sent else passage[:80]).strip()[:120]
            if len(a) < 8:
                continue
            q = "Что сказано в начале фрагмента?"
            parsed = (q, a)
        q, a = parsed
        db.add(
            EvalItem(
                tenant_id=tenant_id,
                eval_set_id=eset.id,
                question=q,
                gold_answers=[a],
                document_id=did,
                split="calibration",
                method="synthetic",
            )
        )
        added += 1
        if added >= limit * per_doc:
            break
    db.commit()
    return {
        "added": added,
        "docs_considered": len(doc_ids),
        "eval_set_id": str(eset.id),
        "errors": errors[:5],
        "offline": True,
    }
