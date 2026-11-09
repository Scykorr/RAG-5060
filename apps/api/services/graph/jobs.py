"""Idempotent graph build jobs — in-process thread (same pattern as quality_loop)."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from apps.api.db import SessionLocal
from apps.api.models import Chunk, Document, DocumentVersion, GraphBuildJob
from apps.api.services.graph import repository as repo
from apps.api.services.graph.detectors.lexical import LexicalSimilarityDetector
from apps.api.services.graph.detectors.references import ExplicitReferenceDetector, build_title_catalog
from apps.api.services.graph.detectors.versions import VersionRelationDetector

log = structlog.get_logger()
_lock = threading.Lock()
_running = False
WORKER_VERSION = "graph-1"


def enqueue_analyze(
    db: Session,
    *,
    tenant_id: UUID,
    mode: str,
    document_id: UUID | None = None,
    source_version_id: UUID | None = None,
    created_by: UUID | None = None,
    request_id: str | None = None,
) -> GraphBuildJob:
    # Dedup: one active job per tenant+doc+mode
    q = db.query(GraphBuildJob).filter(
        GraphBuildJob.tenant_id == tenant_id,
        GraphBuildJob.mode == mode,
        GraphBuildJob.state.in_(("QUEUED", "RUNNING", "RETRYING")),
    )
    if document_id:
        q = q.filter(GraphBuildJob.document_id == document_id)
    else:
        q = q.filter(GraphBuildJob.document_id.is_(None))
    existing = q.order_by(GraphBuildJob.created_at.desc()).first()
    if existing:
        return existing

    job = GraphBuildJob(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        document_id=document_id,
        source_version_id=source_version_id,
        mode=mode,
        state="QUEUED",
        created_by=created_by,
        request_id=request_id,
        worker_version=WORKER_VERSION,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _kick()
    return job


def schedule_after_ingestion(tenant_id: UUID, document_id: UUID, version_id: UUID) -> None:
    def _run() -> None:
        try:
            with SessionLocal() as db:
                enqueue_analyze(
                    db,
                    tenant_id=tenant_id,
                    mode="analyze_new_version",
                    document_id=document_id,
                    source_version_id=version_id,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("graph_schedule_failed", error=type(exc).__name__)

    threading.Thread(target=_run, daemon=True).start()


def _kick() -> None:
    global _running
    with _lock:
        if _running:
            return
        _running = True

    def _worker() -> None:
        global _running
        try:
            while True:
                with SessionLocal() as db:
                    job = (
                        db.query(GraphBuildJob)
                        .filter(GraphBuildJob.state.in_(("QUEUED", "RETRYING")))
                        .order_by(GraphBuildJob.created_at.asc())
                        .first()
                    )
                    if not job:
                        break
                    process_job(db, job.id)
        finally:
            with _lock:
                _running = False

    threading.Thread(target=_worker, daemon=True).start()


def process_job(db: Session, job_id: UUID) -> None:
    job = db.get(GraphBuildJob, job_id)
    if not job:
        return
    if job.state == "CANCELLED":
        return
    job.state = "RUNNING"
    job.attempts += 1
    job.stage = "detect"
    job.lease_until = datetime.now(timezone.utc)
    db.commit()
    try:
        if job.mode in ("analyze_new_version", "rebuild_doc_graph"):
            if not job.document_id:
                raise ValueError("document_id required")
            n = _analyze_document(db, job)
            job.processed = n
            job.total = 1
            job.progress = 100
            job.state = "SUCCEEDED"
        elif job.mode == "rebuild_collection_graph":
            docs = (
                db.query(Document)
                .filter(
                    Document.tenant_id == job.tenant_id,
                    Document.deleted_at.is_(None),
                    Document.status != "deleted",
                    Document.active_version_id.is_not(None),
                )
                .order_by(Document.created_at.asc())
                .limit(500)
                .all()
            )
            job.total = len(docs)
            done = 0
            for doc in docs:
                job.document_id = doc.id
                job.source_version_id = doc.active_version_id
                db.commit()
                _analyze_document(db, job)
                done += 1
                job.processed = done
                job.progress = int(100 * done / max(1, len(docs)))
                db.commit()
            job.state = "SUCCEEDED"
            job.progress = 100
        else:
            job.state = "FAILED"
            job.error_code = "GRAPH_VALIDATION_ERROR"
            job.safe_error = f"Unknown mode: {job.mode}"
        job.finished_at = datetime.now(timezone.utc)
        job.stage = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001
        job.state = "FAILED" if job.attempts >= job.max_attempts else "RETRYING"
        job.error_code = "GRAPH_BUILD_FAILED"
        job.safe_error = type(exc).__name__
        job.finished_at = datetime.now(timezone.utc) if job.state == "FAILED" else None
        db.commit()
        log.warning("graph_job_failed", job_id=str(job_id), error=type(exc).__name__)


def _analyze_document(db: Session, job: GraphBuildJob) -> int:
    assert job.document_id
    doc = db.get(Document, job.document_id)
    if not doc or doc.tenant_id != job.tenant_id or doc.deleted_at:
        return 0
    ver = db.get(DocumentVersion, job.source_version_id or doc.active_version_id)
    if not ver:
        return 0
    chunks = (
        db.query(Chunk)
        .filter(Chunk.version_id == ver.id, Chunk.tenant_id == job.tenant_id)
        .order_by(Chunk.ordinal.asc())
        .all()
    )
    catalog = build_title_catalog(db, job.tenant_id)
    context = {"version": ver, "chunks": chunks, "title_catalog": catalog}
    detectors = [
        VersionRelationDetector(db, job.tenant_id),
        ExplicitReferenceDetector(db, job.tenant_id),
        LexicalSimilarityDetector(db, job.tenant_id),
    ]
    count = 0
    for det in detectors:
        if not det.is_enabled:
            continue
        for cand in det.detect(doc.id, context):
            repo.upsert_candidate(db, tenant_id=job.tenant_id, candidate=cand, run_id=job.id)
            count += 1
    db.commit()
    return count
