"""Load editable language-only lexicon for the connectivity graph (offline).

Corpus-specific authors/titles/frequencies are NEVER stored here — only general
Russian literary language rules (edition markers, play stage directions, genre
words, common toponym lemmas, etc.).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PATH = _ROOT / "configs" / "graph_lexicon.json"


@lru_cache(maxsize=1)
def load_graph_lexicon(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else _DEFAULT_PATH
    if not p.is_file():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def reload_graph_lexicon() -> dict[str, Any]:
    load_graph_lexicon.cache_clear()
    return load_graph_lexicon()


def frozenset_field(key: str) -> frozenset[str]:
    raw = load_graph_lexicon().get(key) or []
    return frozenset(str(x).lower().replace("ё", "е") for x in raw)


def tuple_field(key: str) -> tuple[str, ...]:
    raw = load_graph_lexicon().get(key) or []
    return tuple(str(x) for x in raw)
