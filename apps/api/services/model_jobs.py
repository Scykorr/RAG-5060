"""In-process cancel tokens and helpers for Model Hub import jobs."""

from __future__ import annotations

import hashlib
import threading
import uuid
from pathlib import Path

# job_id -> Event; set() means cancel requested
_CANCEL: dict[uuid.UUID, threading.Event] = {}
_LOCK = threading.Lock()


def register(job_id: uuid.UUID) -> threading.Event:
    ev = threading.Event()
    with _LOCK:
        _CANCEL[job_id] = ev
    return ev


def request_cancel(job_id: uuid.UUID) -> bool:
    with _LOCK:
        ev = _CANCEL.get(job_id)
    if not ev:
        return False
    ev.set()
    return True


def clear(job_id: uuid.UUID) -> None:
    with _LOCK:
        _CANCEL.pop(job_id, None)


def cancelled(job_id: uuid.UUID) -> bool:
    with _LOCK:
        ev = _CANCEL.get(job_id)
    return bool(ev and ev.is_set())


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()
