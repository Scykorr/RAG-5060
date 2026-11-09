from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from apps.api.auth import AuthUser, get_current_user
from apps.api.db import get_db
from apps.api.models import Chunk, Document, DocumentVersion, IngestionJob
from apps.api.services import folder_import as folder_svc
from apps.api.services.ingestion import create_upload
from apps.api.settings import get_settings

router = APIRouter(prefix="/api", tags=["documents"])

ALLOWED = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "application/octet-stream",
}


class FolderImportIn(BaseModel):
    subdir: str = Field(default="", max_length=512, description="Relative path under inbox")
    recursive: bool = True
    skip_existing: bool = True
    # User confirmation from UI preview (required for intentional category choice)
    pack_mode: str | None = Field(default=None, description="existing|new")
    pack_id: str | None = Field(default=None, max_length=64)
    pack_label: str | None = Field(default=None, max_length=200)


class FolderImportPreviewIn(BaseModel):
    subdir: str = Field(default="", max_length=512)
    recursive: bool = True


@router.post("/documents", status_code=202)
async def upload_document(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File()],
):
    settings = get_settings()
    data = await file.read()
    max_b = settings.upload_max_mb * 1024 * 1024
    if len(data) > max_b:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.upload_max_mb} MB")
    name = file.filename or "upload.bin"
    mime = file.content_type or "application/octet-stream"
    if mime not in ALLOWED and not name.lower().endswith((".pdf", ".docx", ".txt")):
        raise HTTPException(status_code=415, detail="Only PDF/DOCX/TXT")
    doc, version, job = create_upload(
        db,
        tenant_id=user.tenant_id,
        owner_id=user.id,
        filename=name,
        mime=mime,
        data=data,
    )
    return {
        "document_id": str(doc.id),
        "version_id": str(version.id),
        "job_id": str(job.id),
        "status": version.status,
        "job_stage": job.stage,
    }


@router.get("/documents/inbox")
def documents_inbox(
    user: Annotated[AuthUser, Depends(get_current_user)],
    subdir: str = "",
    recursive: bool = True,
):
    """List PDF/DOCX/TXT files in the configured import inbox folder."""
    _ = user
    settings = get_settings()
    try:
        files = folder_svc.list_inbox_files(subdir or None, recursive=recursive)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "inbox": str(folder_svc.inbox_root()),
        "host_hint": settings.import_inbox_host_hint,
        "subdir": subdir or "",
        "count": len(files),
        "files": files[:500],
        "import": folder_svc.import_state(),
    }


@router.post("/documents/import-folder/preview")
def import_folder_preview(
    body: FolderImportPreviewIn,
    user: Annotated[AuthUser, Depends(get_current_user)],
):
    """Auto-detect category suggestion + list existing prefixes; no files imported yet."""
    _ = user
    try:
        return folder_svc.preview_folder_import(subdir=body.subdir or None, recursive=body.recursive)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/documents/import-folder", status_code=202)
def import_folder(
    body: FolderImportIn,
    user: Annotated[AuthUser, Depends(get_current_user)],
):
    """Import after user confirms pack_mode/pack_id from preview."""
    try:
        result = folder_svc.start_folder_import(
            tenant_id=user.tenant_id,
            owner_id=user.id,
            subdir=body.subdir or None,
            recursive=body.recursive,
            skip_existing=body.skip_existing,
            pack_mode=body.pack_mode,
            pack_id=body.pack_id,
            pack_label=body.pack_label,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.get("/documents/import-folder/status")
def import_folder_status(user: Annotated[AuthUser, Depends(get_current_user)]):
    _ = user
    return folder_svc.import_state()


@router.get("/documents")
def list_documents(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    q: str = "",
    status: str = "",
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(200, limit))
    offset = max(0, offset)
    base = db.query(Document).filter(
        Document.tenant_id == user.tenant_id,
        Document.deleted_at.is_(None),
        Document.status != "deleted",
    )
    if status.strip():
        base = base.filter(Document.status == status.strip())

    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        # Name match OR chunk text match (local FTS-lite, no external search)
        name_ids = (
            db.query(DocumentVersion.document_id)
            .filter(
                DocumentVersion.tenant_id == user.tenant_id,
                DocumentVersion.original_name.ilike(like),
            )
            .distinct()
        )
        chunk_ids = (
            db.query(Chunk.document_id)
            .filter(Chunk.tenant_id == user.tenant_id, Chunk.text.ilike(like))
            .distinct()
        )
        base = base.filter(or_(Document.id.in_(name_ids), Document.id.in_(chunk_ids)))

    total = base.count()
    chunks_total = (
        db.query(func.count(Chunk.id)).filter(Chunk.tenant_id == user.tenant_id).scalar() or 0
    )
    docs_total = (
        db.query(func.count(Document.id))
        .filter(
            Document.tenant_id == user.tenant_id,
            Document.deleted_at.is_(None),
            Document.status != "deleted",
        )
        .scalar()
        or 0
    )
    rows = base.order_by(Document.created_at.desc()).offset(offset).limit(limit).all()
    items = []
    for d in rows:
        ver = db.get(DocumentVersion, d.active_version_id) if d.active_version_id else None
        items.append(
            {
                "id": str(d.id),
                "status": d.status,
                "active_version_id": str(d.active_version_id) if d.active_version_id else None,
                "original_name": ver.original_name if ver else None,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
        )
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "corpus": {"documents_active": int(docs_total), "chunks": int(chunks_total)},
    }


@router.get("/documents/{document_id}")
def get_document(
    document_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    doc = db.get(Document, document_id)
    if not doc or doc.tenant_id != user.tenant_id or doc.deleted_at:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "id": str(doc.id),
        "status": doc.status,
        "active_version_id": str(doc.active_version_id) if doc.active_version_id else None,
    }


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    doc = db.get(Document, document_id)
    # Uniform 404 — do not reveal cross-tenant existence
    if not doc or doc.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Not found")
    if user.role != "admin" and doc.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    if doc.deleted_at:
        return {"ok": True, "status": "already_deleted"}
    doc.deleted_at = datetime.now(timezone.utc)
    doc.status = "deleted"
    doc.active_version_id = None
    try:
        from apps.api.services.graph.repository import soft_hide_document_edges

        soft_hide_document_edges(db, tenant_id=user.tenant_id, document_id=doc.id)
    except Exception:  # noqa: BLE001
        pass
    db.commit()
    return {"ok": True, "status": "tombstoned"}


@router.get("/jobs/{job_id}")
def get_job(
    job_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    job = db.get(IngestionJob, job_id)
    if not job or job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "id": str(job.id),
        "stage": job.stage,
        "progress": job.progress,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "safe_error": job.safe_error,
    }


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Manual retry of the same logical version (no new version id)."""
    from apps.api.services.ingestion import process_job

    job = db.get(IngestionJob, job_id)
    if not job or job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Not found")
    doc = db.get(Document, job.document_id)
    if not doc or doc.deleted_at or doc.status == "deleted":
        raise HTTPException(status_code=409, detail="Document deleted")
    if job.attempts >= job.max_attempts and job.stage == "failed":
        raise HTTPException(status_code=429, detail="Max attempts exceeded")
    job.stage = "queued"
    job.safe_error = None
    job.finished_at = None
    job.progress = 0
    db.commit()
    process_job(db, job.id)
    db.refresh(job)
    return {
        "id": str(job.id),
        "stage": job.stage,
        "progress": job.progress,
        "attempts": job.attempts,
        "safe_error": job.safe_error,
    }
