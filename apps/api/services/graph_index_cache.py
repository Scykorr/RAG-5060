"""Per-document artifacts + build status for connectivity index lifecycle.

Offline only. Doc caches live under data_root()/graph/doc_cache/<tenant>/.
Build status: data_root()/graph/build_status/<tenant>.json
Current (last-good) pointer: data_root()/graph/current/<tenant>.json
Lock file: data_root()/graph/locks/<tenant>.lock
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from apps.api.services.storage import data_root

KEEP_INDEX_FILES = 3
LOCK_TIMEOUT_SEC = 30 * 60  # 30 minutes
LOCK_STALE_SEC = LOCK_TIMEOUT_SEC

_status_lock = threading.Lock()
_tenant_locks: dict[str, threading.Lock] = {}
_running: dict[str, bool] = {}


def graph_dir() -> Path:
    p = data_root() / "graph"
    p.mkdir(parents=True, exist_ok=True)
    return p


def doc_cache_dir(tenant_id: UUID | str) -> Path:
    p = graph_dir() / "doc_cache" / str(tenant_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def build_status_path(tenant_id: UUID | str) -> Path:
    d = graph_dir() / "build_status"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{tenant_id}.json"


def current_pointer_path(tenant_id: UUID | str) -> Path:
    d = graph_dir() / "current"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{tenant_id}.json"


def lock_path(tenant_id: UUID | str) -> Path:
    d = graph_dir() / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{tenant_id}.lock"


def tenant_lock(tenant_id: UUID | str) -> threading.Lock:
    """In-process mutex (complements file lock across workers/restarts)."""
    key = str(tenant_id)
    with _status_lock:
        if key not in _tenant_locks:
            _tenant_locks[key] = threading.Lock()
        return _tenant_locks[key]


def is_build_running(tenant_id: UUID | str) -> bool:
    with _status_lock:
        if bool(_running.get(str(tenant_id))):
            return True
    # file lock may outlive process flag after crash recovery checks
    return lock_held(tenant_id)


def set_build_running(tenant_id: UUID | str, running: bool) -> None:
    with _status_lock:
        _running[str(tenant_id)] = running


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock(tenant_id: UUID | str) -> dict[str, Any] | None:
    path = lock_path(tenant_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def lock_held(tenant_id: UUID | str, *, timeout_sec: float = LOCK_TIMEOUT_SEC) -> bool:
    data = _read_lock(tenant_id)
    if not data:
        return False
    pid = int(data.get("pid") or 0)
    started_at = data.get("started_at")
    age = timeout_sec + 1
    if started_at:
        try:
            started_dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - started_dt).total_seconds()
        except ValueError:
            age = timeout_sec + 1
    if age > timeout_sec:
        return False
    if pid and not _pid_alive(pid):
        return False
    return True


def clear_dead_lock(tenant_id: UUID | str, *, timeout_sec: float = LOCK_TIMEOUT_SEC) -> bool:
    """Remove lock if missing/expired/dead pid. Returns True if lock file cleared."""
    path = lock_path(tenant_id)
    if not path.exists():
        return True
    if lock_held(tenant_id, timeout_sec=timeout_sec):
        return False
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def acquire_build_lock(
    tenant_id: UUID | str,
    *,
    timeout_sec: float = LOCK_TIMEOUT_SEC,
) -> bool:
    """Exclusive per-tenant file lock under data_root()/graph/locks/."""
    tid = str(tenant_id)
    clear_dead_lock(tid, timeout_sec=timeout_sec)
    path = lock_path(tid)
    if path.exists() and lock_held(tid, timeout_sec=timeout_sec):
        return False
    payload = {
        "tenant_id": tid,
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "started_mono": time.monotonic(),
        "timeout_sec": timeout_sec,
    }
    tmp = path.with_suffix(".lock.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        # exclusive create race: if exists after clear, refuse
        if path.exists() and lock_held(tid, timeout_sec=timeout_sec):
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def release_build_lock(tenant_id: UUID | str) -> None:
    path = lock_path(tenant_id)
    data = _read_lock(tenant_id)
    if data and int(data.get("pid") or 0) not in (0, os.getpid()):
        # do not remove another live owner's lock
        if lock_held(tenant_id):
            return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def write_json_atomic(path: Path, payload: dict[str, Any] | list[Any], *, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=indent)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_build_status(tenant_id: UUID | str, payload: dict[str, Any]) -> None:
    path = build_status_path(tenant_id)
    data = dict(payload)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(path, data)


def read_build_status(tenant_id: UUID | str) -> dict[str, Any] | None:
    path = build_status_path(tenant_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_current_pointer(tenant_id: UUID | str, payload: dict[str, Any]) -> None:
    write_json_atomic(current_pointer_path(tenant_id), payload)


def read_current_pointer(tenant_id: UUID | str) -> dict[str, Any] | None:
    path = current_pointer_path(tenant_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def mark_index_stale(tenant_id: UUID | str, *, reason: str = "corpus_changed") -> dict[str, Any]:
    """Flag last-good index as stale after ingestion; do not delete it."""
    ptr = read_current_pointer(tenant_id) or {}
    ptr["stale"] = True
    ptr["stale_reason"] = reason[:200]
    ptr["stale_at"] = datetime.now(timezone.utc).isoformat()
    write_current_pointer(tenant_id, ptr)
    st = read_build_status(tenant_id) or {}
    st["stale"] = True
    st["stale_reason"] = reason[:200]
    if st.get("status") not in ("running",):
        st["status"] = st.get("status") or "stale"
    write_build_status(tenant_id, st)
    return ptr


def clear_stale_flag(tenant_id: UUID | str) -> None:
    ptr = read_current_pointer(tenant_id) or {}
    if ptr:
        ptr["stale"] = False
        ptr.pop("stale_reason", None)
        ptr.pop("stale_at", None)
        write_current_pointer(tenant_id, ptr)


def params_hash(params_dict: dict[str, Any], *, lexicon_hash: str | None = None) -> str:
    """Hash of build params + lexicon + overrides file content."""
    from apps.api.services.graph_lexicon import lexicon_content_hash

    payload = dict(params_dict)
    payload["lexicon_sha1"] = lexicon_hash or lexicon_content_hash()
    try:
        from apps.api.services.graph_overrides import load_overrides

        ov = load_overrides()
        payload["overrides_sha1"] = hashlib.sha1(
            json.dumps(ov, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        payload["overrides_sha1"] = "none"
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def content_hash(*, version_id: str, filename: str, chunk_texts: list[str]) -> str:
    h = hashlib.sha1()
    h.update(f"{version_id}|{filename}|{len(chunk_texts)}|".encode("utf-8"))
    for t in chunk_texts:
        h.update(b"\n")
        h.update((t or "").encode("utf-8"))
    return h.hexdigest()


def doc_artifact_path(tenant_id: UUID | str, document_id: str) -> Path:
    return doc_cache_dir(tenant_id) / f"{document_id}.json"


def read_doc_artifact(tenant_id: UUID | str, document_id: str) -> dict[str, Any] | None:
    path = doc_artifact_path(tenant_id, document_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_doc_artifact(tenant_id: UUID | str, document_id: str, payload: dict[str, Any]) -> None:
    write_json_atomic(doc_artifact_path(tenant_id, document_id), payload, indent=None)


def purge_doc_artifacts(tenant_id: UUID | str, keep_ids: set[str]) -> int:
    removed = 0
    d = doc_cache_dir(tenant_id)
    for p in d.glob("*.json"):
        if p.stem not in keep_ids:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def invalidate_tenant_graph(tenant_id: UUID | str) -> dict[str, int]:
    """Drop indexes, doc caches, build status, pointer, lock for ONE tenant only."""
    tid = str(tenant_id)
    removed_idx = 0
    for p in graph_dir().glob(f"{tid}_*_index.json"):
        try:
            p.unlink()
            removed_idx += 1
        except OSError:
            pass
    removed_docs = 0
    d = graph_dir() / "doc_cache" / tid
    if d.exists():
        for p in d.glob("*.json"):
            try:
                p.unlink()
                removed_docs += 1
            except OSError:
                pass
    topics_removed = 0
    topics_file = graph_dir() / "topics" / f"{tid}.json"
    for extra in (topics_file, topics_file.with_suffix(".json.tmp")):
        if extra.exists():
            try:
                extra.unlink()
                topics_removed += 1
            except OSError:
                pass
    for helper in (build_status_path(tid), current_pointer_path(tid), lock_path(tid)):
        if helper.exists():
            try:
                helper.unlink()
            except OSError:
                pass
    write_build_status(
        tid,
        {
            "status": "idle",
            "mode": None,
            "message": "invalidated",
            "stale": False,
        },
    )
    return {
        "indexes_removed": removed_idx,
        "doc_artifacts_removed": removed_docs,
        "topics_removed": topics_removed,
    }


def index_filename(tenant_id: UUID | str, fingerprint: str, index_version: int, p_hash: str) -> str:
    return f"{tenant_id}_{fingerprint}_v{index_version}_{p_hash}_index.json"


def index_path(
    tenant_id: UUID | str,
    fingerprint: str,
    *,
    index_version: int,
    params_hash_value: str,
) -> Path:
    return graph_dir() / index_filename(tenant_id, fingerprint, index_version, params_hash_value)


def purge_old_indexes(
    tenant_id: UUID | str,
    keep_path: Path,
    *,
    keep_n: int = KEEP_INDEX_FILES,
) -> int:
    """Always keep ``keep_path``; additionally keep up to ``keep_n-1`` newest others.

    Only touches files for this tenant_id prefix.
    """
    tid = str(tenant_id)
    keep_res = keep_path.resolve()
    files = sorted(
        graph_dir().glob(f"{tid}_*_index.json"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    keep_set = {keep_res}
    for p in files:
        if len(keep_set) >= max(1, keep_n):
            break
        keep_set.add(p.resolve())
    keep_set.add(keep_res)
    removed = 0
    for p in files:
        if p.resolve() in keep_set:
            continue
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def timer() -> float:
    return time.perf_counter()
