"""Mark quality stale and optionally auto-start improve after corpus changes."""

from __future__ import annotations

import threading
import uuid
from typing import Any

from apps.api.db import SessionLocal
from apps.api.models import Document, IngestionJob
from apps.api.services import quality_loop as qloop
from apps.api.services import quality_state as qstate

_debounce_lock = threading.Lock()
_timer: threading.Timer | None = None


def notify_corpus_changed(tenant_id: uuid.UUID, *, reason: str = "corpus_changed", delay_sec: float = 8.0) -> None:
    """Debounced: wait for ingestion burst to settle, then stale + maybe auto-run."""

    def _fire():
        s = SessionLocal()
        try:
            # Wait until no active ingestion for this tenant
            active = (
                s.query(IngestionJob)
                .filter(
                    IngestionJob.tenant_id == tenant_id,
                    IngestionJob.stage.in_(["queued", "parsing", "chunking", "embedding", "indexing"]),
                )
                .count()
            )
            if active > 0:
                notify_corpus_changed(tenant_id, reason=reason, delay_sec=5.0)
                return
            fp = qstate.corpus_fingerprint(s, tenant_id)
            last = qstate._get(s, "quality_corpus_fingerprint", "")
            qstate.mark_stale(s, reason=reason)
            s.commit()
            try:
                from apps.api.services import graph_index as gidx

                gidx.on_corpus_changed(tenant_id, reason=reason)
            except Exception:  # noqa: BLE001
                pass
            if not qstate.auto_improve_enabled(s):
                return
            if last and last == fp and qstate._get(s, "quality_status", "") == "ok":
                return
            docs = (
                s.query(Document)
                .filter(
                    Document.tenant_id == tenant_id,
                    Document.deleted_at.is_(None),
                    Document.status != "deleted",
                )
                .count()
            )
            if docs <= 0:
                return
            qloop.start_quality_run(s, tenant_id=tenant_id, auto_synth_if_empty=True)
        except Exception:  # noqa: BLE001
            pass
        finally:
            s.close()

    global _timer
    with _debounce_lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(delay_sec, _fire)
        _timer.daemon = True
        _timer.start()


def after_ingestion_ready(tenant_id: uuid.UUID) -> None:
    notify_corpus_changed(tenant_id, reason="ingestion_ready")


def after_folder_import_done(tenant_id: uuid.UUID) -> dict[str, Any]:
    notify_corpus_changed(tenant_id, reason="folder_import_done", delay_sec=3.0)
    return {"scheduled": True, "offline": True}
