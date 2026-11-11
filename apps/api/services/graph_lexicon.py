"""Load editable language-only lexicon for the connectivity graph (offline).

Corpus-specific authors/titles/frequencies are NEVER stored here — only general
Russian literary language rules (edition markers, play stage directions, genre
words, common toponym lemmas, etc.).
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PATH = _ROOT / "configs" / "graph_lexicon.json"


def lexicon_path(path: str | None = None) -> Path:
    return Path(path) if path else _DEFAULT_PATH


@lru_cache(maxsize=1)
def load_graph_lexicon(path: str | None = None) -> dict[str, Any]:
    p = lexicon_path(path)
    if not p.is_file():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def lexicon_content_hash(path: str | None = None) -> str:
    """SHA1 of graph_lexicon.json bytes — feeds params_hash / cache invalidation."""
    p = lexicon_path(path)
    if not p.is_file():
        return "missing"
    return hashlib.sha1(p.read_bytes()).hexdigest()[:12]


def reload_graph_lexicon() -> dict[str, Any]:
    load_graph_lexicon.cache_clear()
    lexicon_content_hash.cache_clear()
    return load_graph_lexicon()


def frozenset_field(key: str) -> frozenset[str]:
    raw = load_graph_lexicon().get(key) or []
    return frozenset(str(x).lower().replace("ё", "е") for x in raw)


def tuple_field(key: str) -> tuple[str, ...]:
    raw = load_graph_lexicon().get(key) or []
    return tuple(str(x) for x in raw)


def dict_field(key: str) -> dict[str, Any]:
    raw = load_graph_lexicon().get(key) or {}
    return raw if isinstance(raw, dict) else {}
