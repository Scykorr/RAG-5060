"""Optional manual overrides for connectivity index (slice 1e E7)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PATH = _ROOT / "configs" / "graph_overrides.json"


@lru_cache(maxsize=1)
def load_overrides(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else _DEFAULT_PATH
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def reload_overrides() -> dict[str, Any]:
    load_overrides.cache_clear()
    return load_overrides()


def apply_overrides(docs: list[Any]) -> list[dict[str, Any]]:
    """Apply filename/work_key author overrides. Returns list of applied rules."""
    cfg = load_overrides()
    by_prefix = cfg.get("authors_by_filename_prefix") or {}
    by_work = cfg.get("authors_by_work_key") or {}
    applied: list[dict[str, Any]] = []
    if not by_prefix and not by_work:
        return applied

    for d in docs:
        fn = getattr(d, "filename", "") or ""
        wk = getattr(d, "work_key", "") or ""
        matched = False
        new_ak: str | None = None
        rule = None
        for pref, ak in by_prefix.items():
            if pref and fn.startswith(str(pref)):
                matched = True
                new_ak = None if ak is None else str(ak)
                rule = {"type": "filename_prefix", "prefix": pref}
                break
        if not matched and wk in by_work:
            matched = True
            ak = by_work[wk]
            new_ak = None if ak is None else str(ak)
            rule = {"type": "work_key", "work_key": wk}
        if not matched or not rule:
            continue
        # null override → clear author (e.g. sample docs)
        d.author_key = new_ak or None
        d.author_source = "override"
        d.author_ru = None if not new_ak else getattr(d, "author_ru", None)
        # recompute work_key with new author
        from apps.api.services.graph_index import work_key_for

        d.work_key = work_key_for(pack=d.pack, author_key=d.author_key, title=d.title)
        applied.append({"document_id": d.document_id, "author_key": new_ak, **rule})

    # merge_works: list of {from: work_key, to: work_key}
    for rule in cfg.get("merge_works") or []:
        src = rule.get("from")
        dst = rule.get("to")
        if not src or not dst:
            continue
        for d in docs:
            if d.work_key == src:
                d.work_key = dst
                d.author_source = d.author_source or "override"
                applied.append({"document_id": d.document_id, "merge": src, "to": dst})
    return applied


def apply_doc_kind_overrides(docs: list[Any]) -> list[dict[str, Any]]:
    """Optional doc_kind / language overrides from graph_overrides.json."""
    cfg = load_overrides()
    by_prefix = cfg.get("doc_kind_by_filename_prefix") or {}
    by_work = cfg.get("doc_kind_by_work_key") or {}
    lang_prefix = cfg.get("language_by_filename_prefix") or {}
    applied: list[dict[str, Any]] = []
    for d in docs:
        fn = getattr(d, "filename", "") or ""
        wk = getattr(d, "work_key", "") or ""
        for pref, kind in by_prefix.items():
            if pref and fn.startswith(str(pref)):
                d.doc_kind = str(kind)
                applied.append({"document_id": d.document_id, "doc_kind": kind, "via": "prefix"})
                break
        if wk in by_work:
            d.doc_kind = str(by_work[wk])
            applied.append({"document_id": d.document_id, "doc_kind": by_work[wk], "via": "work_key"})
        for pref, lang in lang_prefix.items():
            if pref and fn.startswith(str(pref)):
                d.language = str(lang)
                applied.append({"document_id": d.document_id, "language": lang, "via": "prefix"})
                break
    return applied
