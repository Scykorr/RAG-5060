"""Near-duplicate edition detection within a work (slice 1d A6).

Stdlib-only shingle hashes — no extra dependencies.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable

_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+", re.UNICODE)


def tokenize_shingles(text: str, *, n: int = 5) -> list[str]:
    toks = [t.lower().replace("ё", "е") for t in _TOKEN_RE.findall(text or "")]
    if len(toks) < n:
        return [" ".join(toks)] if toks else []
    return [" ".join(toks[i : i + n]) for i in range(len(toks) - n + 1)]


def shingle_fingerprint(text: str, *, n: int = 5, sample: int = 64) -> set[str]:
    """Hash shingles and keep a deterministic sample (minhash-like)."""
    shingles = tokenize_shingles(text, n=n)
    if not shingles:
        return set()
    hashed = sorted(hashlib.md5(s.encode("utf-8")).hexdigest() for s in shingles)
    if len(hashed) <= sample:
        return set(hashed)
    # even sample across ranked hashes
    step = max(1, len(hashed) // sample)
    return set(hashed[::step][:sample])


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def mark_canonical_duplicates(
    docs: list[Any],
    texts_by_id: dict[str, str],
    *,
    threshold: float = 0.85,
) -> dict[str, dict[str, Any]]:
    """Within each work_key, pick canonical and mark near-duplicates.

    Returns document_id → {canonical: bool, duplicate_of: id|None, sim: float}.
    Prefers longer text as canonical; ties → smaller document_id.
    """
    by_work: dict[str, list[Any]] = defaultdict(list)
    for d in docs:
        by_work[getattr(d, "work_key", "") or "_"].append(d)

    out: dict[str, dict[str, Any]] = {}
    fps: dict[str, set[str]] = {
        d.document_id: shingle_fingerprint(texts_by_id.get(d.document_id, ""))
        for d in docs
    }

    for _wk, group in by_work.items():
        if len(group) == 1:
            d = group[0]
            out[d.document_id] = {"canonical": True, "duplicate_of": None, "sim": 1.0}
            continue
        # sort: longer text first, then id
        ranked = sorted(
            group,
            key=lambda d: (
                -len(texts_by_id.get(d.document_id, "") or ""),
                d.document_id,
            ),
        )
        canon_ids: list[str] = []
        for d in ranked:
            did = d.document_id
            fp = fps.get(did) or set()
            best_sim = 0.0
            best_of = None
            for cid in canon_ids:
                sim = jaccard(fp, fps.get(cid) or set())
                if sim > best_sim:
                    best_sim = sim
                    best_of = cid
            if best_of is not None and best_sim >= threshold:
                out[did] = {
                    "canonical": False,
                    "duplicate_of": best_of,
                    "sim": round(best_sim, 4),
                }
            else:
                canon_ids.append(did)
                out[did] = {"canonical": True, "duplicate_of": None, "sim": 1.0}
    # ensure every doc present
    for d in docs:
        out.setdefault(
            d.document_id, {"canonical": True, "duplicate_of": None, "sim": 1.0}
        )
    return out


def canonical_doc_ids(dup_map: dict[str, dict[str, Any]]) -> set[str]:
    return {did for did, info in dup_map.items() if info.get("canonical")}
