"""Per-document artifacts + build status for connectivity index lifecycle.

Offline only. Doc caches live under data_root()/graph/doc_cache/<tenant>/.
Build status: data_root()/graph/build_status/<tenant>.json
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from apps.api.services.storage import data_root

KEEP_INDEX_FILES = 3

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


def tenant_lock(tenant_id: UUID | str) -> threading.Lock:
    key = str(tenant_id)
    with _status_lock:
        if key not in _tenant_locks:
            _tenant_locks[key] = threading.Lock()
        return _tenant_locks[key]


def is_build_running(tenant_id: UUID | str) -> bool:
    with _status_lock:
        return bool(_running.get(str(tenant_id)))


def set_build_running(tenant_id: UUID | str, running: bool) -> None:
    with _status_lock:
        _running[str(tenant_id)] = running


def write_build_status(tenant_id: UUID | str, payload: dict[str, Any]) -> None:
    path = build_status_path(tenant_id)
    tmp = path.with_suffix(".tmp")
    data = dict(payload)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_build_status(tenant_id: UUID | str) -> dict[str, Any] | None:
    path = build_status_path(tenant_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def params_hash(params_dict: dict[str, Any]) -> str:
    raw = json.dumps(params_dict, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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
    path = doc_artifact_path(tenant_id, document_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


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
    """Drop indexes, doc caches, build status for tenant (reset-rag / empty)."""
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
    sp = build_status_path(tid)
    if sp.exists():
        try:
            sp.unlink()
        except OSError:
            pass
    write_build_status(
        tid,
        {
            "status": "idle",
            "mode": None,
            "message": "invalidated",
        },
    )
    return {"indexes_removed": removed_idx, "doc_artifacts_removed": removed_docs}


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
    """Always keep ``keep_path``; additionally keep up to ``keep_n-1`` newest others."""
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
