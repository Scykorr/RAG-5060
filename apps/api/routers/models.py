from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.orm import Session

from apps.api.auth import AuthUser, require_admin
from apps.api.db import SessionLocal, get_db
from apps.api.models import AppSetting, ModelImportJob
from apps.api.services import model_jobs as jobctl
from apps.api.services import ollama as ollama_svc
from apps.api.services.storage import data_root
from apps.api.settings import get_settings

router = APIRouter(prefix="/api/admin/models", tags=["models"])


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class UrlIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    sha256: str | None = Field(default=None, max_length=64)


def _get_active(db: Session) -> str:
    row = db.get(AppSetting, "active_model")
    return row.value if row else "qwen3:8b"


def _set_active(db: Session, name: str) -> None:
    row = db.get(AppSetting, "active_model")
    if row:
        row.value = name
    else:
        db.add(AppSetting(key="active_model", value=name))
    db.commit()


def _max_bytes() -> int:
    return int(get_settings().model_import_max_gb * (1024**3))


def _finish_job(s: Session, jid: uuid.UUID, *, status: str, progress: int | None = None, detail: str | None = None):
    row = s.get(ModelImportJob, jid)
    if not row:
        return
    # Do not resurrect a cancelled job as done/failed from a late worker.
    if row.status == "cancelled" and status == "done":
        return
    row.status = status
    if progress is not None:
        row.progress = progress
    if detail is not None:
        row.detail = detail[:500]
    row.finished_at = datetime.now(timezone.utc)
    s.commit()


def _update_progress(s: Session, jid: uuid.UUID, progress: int, detail: str | None = None):
    row = s.get(ModelImportJob, jid)
    if not row or row.status in ("done", "failed", "cancelled"):
        return
    row.progress = max(0, min(99, progress))
    if detail is not None:
        row.detail = detail[:500]
    s.commit()


@router.get("")
def list_models_ep(_: Annotated[AuthUser, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    try:
        models = ollama_svc.list_models()
    except Exception as exc:  # noqa: BLE001
        models = []
        detail = type(exc).__name__
    else:
        detail = None
    return {"active": _get_active(db), "models": models, "error": detail}


@router.post("/pull")
def pull_model(
    body: NameIn,
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    job = ModelImportJob(name=body.name, source="pull", status="running")
    db.add(job)
    db.commit()
    db.refresh(job)
    jid = job.id
    cancel = jobctl.register(jid)

    def _run():
        s = SessionLocal()
        try:
            if cancel.is_set():
                _finish_job(s, jid, status="cancelled", progress=0, detail="cancelled")
                return
            ollama_svc.pull_model(body.name)
            if cancel.is_set():
                _finish_job(s, jid, status="cancelled", progress=0, detail="cancelled after pull")
                return
            _finish_job(s, jid, status="done", progress=100)
        except Exception as exc:  # noqa: BLE001
            _finish_job(s, jid, status="failed", detail=str(exc))
        finally:
            jobctl.clear(jid)
            s.close()

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": str(job.id), "status": "running"}


@router.post("/activate")
def activate(
    body: NameIn,
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    names = {m["name"] for m in ollama_svc.list_models()}
    if body.name not in names:
        raise HTTPException(status_code=404, detail="Model not found in Ollama")
    old = _get_active(db)
    if old and old != body.name:
        ollama_svc.unload(old)
    try:
        out = ollama_svc.generate("Reply with exactly: OK", body.name, think=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Smoke generate failed: {exc}") from exc
    _set_active(db, body.name)
    return {"active": body.name, "smoke": (out or "")[:200]}


@router.post("/import-gguf")
async def import_gguf(
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
):
    if not file.filename or not file.filename.lower().endswith(".gguf"):
        raise HTTPException(status_code=400, detail="Expected .gguf file")
    data = await file.read()
    if len(data) > _max_bytes():
        raise HTTPException(
            status_code=413,
            detail=f"GGUF exceeds MODEL_IMPORT_MAX_GB={get_settings().model_import_max_gb}",
        )
    dest = data_root() / "imports" / f"{name}.gguf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    job = ModelImportJob(name=name, source="gguf", status="running", progress=10)
    db.add(job)
    db.commit()
    db.refresh(job)
    jid = job.id
    cancel = jobctl.register(jid)

    def _run():
        s = SessionLocal()
        try:
            if cancel.is_set():
                _finish_job(s, jid, status="cancelled", detail="cancelled")
                return
            ollama_svc.create_from_gguf(name, dest)
            if cancel.is_set():
                _finish_job(s, jid, status="cancelled", detail="cancelled after create")
                return
            _finish_job(s, jid, status="done", progress=100)
        except Exception as exc:  # noqa: BLE001
            _finish_job(s, jid, status="failed", detail=str(exc))
        finally:
            jobctl.clear(jid)
            s.close()

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": str(job.id), "path": str(dest)}


@router.post("/import-url")
def import_url(
    body: UrlIn,
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    parsed = urlparse(str(body.url))
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http(s) URLs allowed")
    expected = (body.sha256 or "").strip().lower() or None
    if expected and len(expected) != 64:
        raise HTTPException(status_code=400, detail="sha256 must be 64 hex chars")
    job = ModelImportJob(
        name=body.name,
        source="url",
        status="running",
        detail=f"download {parsed.netloc}",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    jid = job.id
    cancel = jobctl.register(jid)
    dest = data_root() / "imports" / f"{body.name}.gguf"
    max_b = _max_bytes()

    def _run():
        s = SessionLocal()
        try:
            with httpx.stream("GET", str(body.url), follow_redirects=True, timeout=None) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                if total and total > max_b:
                    raise RuntimeError(f"Remote size {total} exceeds MODEL_IMPORT_MAX_GB limit")
                dest.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                last_pct = -1
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(1024 * 1024):
                        if cancel.is_set():
                            f.close()
                            dest.unlink(missing_ok=True)
                            _finish_job(s, jid, status="cancelled", detail="cancelled during download")
                            return
                        f.write(chunk)
                        written += len(chunk)
                        if written > max_b:
                            raise RuntimeError("Download exceeded MODEL_IMPORT_MAX_GB limit")
                        if total:
                            pct = int(written * 90 / total)
                        else:
                            pct = min(89, written // (50 * 1024 * 1024))
                        if pct != last_pct and pct % 5 == 0:
                            last_pct = pct
                            _update_progress(s, jid, pct, detail=f"downloaded {written} bytes")
            if expected:
                digest = jobctl.sha256_file(dest)
                if digest != expected:
                    dest.unlink(missing_ok=True)
                    raise RuntimeError(f"checksum mismatch: got {digest[:16]}…")
                _update_progress(s, jid, 92, detail="checksum ok")
            if cancel.is_set():
                dest.unlink(missing_ok=True)
                _finish_job(s, jid, status="cancelled", detail="cancelled before create")
                return
            _update_progress(s, jid, 95, detail="ollama create")
            ollama_svc.create_from_gguf(body.name, dest)
            _finish_job(s, jid, status="done", progress=100, detail="imported")
        except Exception as exc:  # noqa: BLE001
            dest.unlink(missing_ok=True)
            _finish_job(s, jid, status="failed", detail=str(exc))
        finally:
            jobctl.clear(jid)
            s.close()

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": str(job.id), "max_bytes": max_b}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: uuid.UUID,
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    job = db.get(ModelImportJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Not found")
    if job.status in ("done", "failed", "cancelled"):
        return {"id": str(job.id), "status": job.status, "cancelled": False}
    requested = jobctl.request_cancel(job_id)
    job.status = "cancelled"
    job.detail = (job.detail or "")[:400] + " | cancel requested"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": str(job.id), "status": "cancelled", "cancel_signaled": requested}


@router.get("/jobs/{job_id}")
def job_status(
    job_id: uuid.UUID,
    _: Annotated[AuthUser, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    job = db.get(ModelImportJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "id": str(job.id),
        "name": job.name,
        "source": job.source,
        "status": job.status,
        "progress": job.progress,
        "detail": job.detail,
    }
