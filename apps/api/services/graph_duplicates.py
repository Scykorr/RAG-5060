"""Near-duplicate + containment detection (slice 1e E1).

Stdlib-only: 8-word shingles, 128-value minhash, LSH buckets.
Compares candidates within work_key and same-author / authorless buckets —
no full N² scan.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+", re.UNICODE)

SHINGLE_N = 8
MINHASH_SIZE = 128
LSH_BANDS = 16  # 16 × 8 = 128
LSH_ROWS = MINHASH_SIZE // LSH_BANDS
DUP_JACCARD = 0.80
CONTAIN_COVERAGE = 0.80


def tokenize_words(text: str) -> list[str]:
    return [t.lower().replace("ё", "е") for t in _TOKEN_RE.findall(text or "")]


def tokenize_shingles(text: str, *, n: int = SHINGLE_N) -> list[str]:
    toks = tokenize_words(text)
    if len(toks) < n:
        return [" ".join(toks)] if toks else []
    return [" ".join(toks[i : i + n]) for i in range(len(toks) - n + 1)]


def _h64(s: str, seed: int) -> int:
    raw = hashlib.md5(f"{seed}:{s}".encode("utf-8")).digest()
    return int.from_bytes(raw[:8], "big")


def minhash_signature(shingles: list[str] | set[str], *, size: int = MINHASH_SIZE) -> tuple[int, ...]:
    if not shingles:
        return tuple(0 for _ in range(size))
    sig = [2**63 - 1] * size
    for sh in shingles:
        for i in range(size):
            v = _h64(sh, i)
            if v < sig[i]:
                sig[i] = v
    return tuple(sig)


def shingle_set(text: str, *, n: int = SHINGLE_N, max_keep: int = 4000) -> set[str]:
    """Exact shingle strings; subsample deterministically if huge."""
    sh = tokenize_shingles(text, n=n)
    if len(sh) <= max_keep:
        return set(sh)
    hashed = sorted((hashlib.md5(s.encode("utf-8")).hexdigest(), s) for s in sh)
    step = max(1, len(hashed) // max_keep)
    return {s for _, s in hashed[::step][:max_keep]}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def containment(small: set[str], big: set[str]) -> float:
    """Fraction of small's shingles found in big."""
    if not small:
        return 0.0
    return len(small & big) / len(small)


def shingle_fingerprint(text: str, *, n: int = 5, sample: int = 64) -> set[str]:
    """Back-compat helper used by older tests (hash sample of n-shingles)."""
    shingles = tokenize_shingles(text, n=n)
    if not shingles:
        return set()
    hashed = sorted(hashlib.md5(s.encode("utf-8")).hexdigest() for s in shingles)
    if len(hashed) <= sample:
        return set(hashed)
    step = max(1, len(hashed) // sample)
    return set(hashed[::step][:sample])


def _lsh_buckets(sig: tuple[int, ...]) -> list[str]:
    out: list[str] = []
    for b in range(LSH_BANDS):
        start = b * LSH_ROWS
        band = sig[start : start + LSH_ROWS]
        key = hashlib.md5(f"{b}:{band}".encode("utf-8")).hexdigest()[:16]
        out.append(f"b{b}:{key}")
    return out


def _candidate_groups(docs: list[Any]) -> list[list[Any]]:
    """Groups to compare: each work_key, each author_key, authorless pool."""
    by_work: dict[str, list[Any]] = defaultdict(list)
    by_author: dict[str, list[Any]] = defaultdict(list)
    authorless: list[Any] = []
    for d in docs:
        by_work[getattr(d, "work_key", "") or "_"].append(d)
        ak = getattr(d, "author_key", None)
        if ak:
            by_author[str(ak)].append(d)
        else:
            authorless.append(d)
    groups: list[list[Any]] = []
    for g in by_work.values():
        if len(g) >= 2:
            groups.append(g)
    for g in by_author.values():
        if len(g) >= 2:
            groups.append(g)
    if len(authorless) >= 2:
        groups.append(authorless)
    return groups


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def detect_relations(
    docs: list[Any],
    texts_by_id: dict[str, str],
    *,
    dup_threshold: float = DUP_JACCARD,
    contain_threshold: float = CONTAIN_COVERAGE,
) -> dict[str, dict[str, Any]]:
    """Return document_id → relation info.

    Keys: canonical, duplicate_of, contained_in, sim, coverage, relation
    (canonical | duplicate | contained_in).
    """
    ids = [d.document_id for d in docs]
    sets: dict[str, set[str]] = {}
    sigs: dict[str, tuple[int, ...]] = {}
    sizes: dict[str, int] = {}
    for d in docs:
        did = d.document_id
        text = texts_by_id.get(did, "") or ""
        s = shingle_set(text)
        sets[did] = s
        sizes[did] = len(tokenize_words(text))
        sigs[did] = minhash_signature(s)

    # LSH buckets within each candidate group
    pair_scores: dict[tuple[str, str], dict[str, float]] = {}

    def _score_pair(a: str, b: str) -> None:
        pk = _pair_key(a, b)
        if pk in pair_scores:
            return
        # Metrics always in sorted key order (pk[0], pk[1]).
        x, y = pk
        sx, sy = sets[x], sets[y]
        pair_scores[pk] = {
            "jaccard": jaccard(sx, sy),
            "cov_ab": containment(sx, sy),  # x inside y
            "cov_ba": containment(sy, sx),  # y inside x
            "size_a": sizes[x],
            "size_b": sizes[y],
        }

    for group in _candidate_groups(docs):
        buckets: dict[str, list[str]] = defaultdict(list)
        for d in group:
            for bk in _lsh_buckets(sigs[d.document_id]):
                buckets[bk].append(d.document_id)
        for members in buckets.values():
            uniq = sorted(set(members))
            if len(uniq) < 2:
                continue
            for i, a in enumerate(uniq[:48]):
                for b in uniq[i + 1 : 48]:
                    _score_pair(a, b)
        # Small groups: exhaustive (work / author / authorless)
        if 2 <= len(group) <= 60:
            gids = [d.document_id for d in group]
            for i, a in enumerate(gids):
                for b in gids[i + 1 :]:
                    _score_pair(a, b)

    # Same work_key always exhaustively up to 120 (Anna fragments)
    by_work: dict[str, list[Any]] = defaultdict(list)
    for d in docs:
        by_work[getattr(d, "work_key", "") or "_"].append(d)
    for group in by_work.values():
        if len(group) < 2 or len(group) > 120:
            continue
        gids = [d.document_id for d in group]
        for i, a in enumerate(gids):
            for b in gids[i + 1 :]:
                _score_pair(a, b)

    out: dict[str, dict[str, Any]] = {
        did: {
            "canonical": True,
            "duplicate_of": None,
            "contained_in": None,
            "sim": 1.0,
            "coverage": 0.0,
            "relation": "canonical",
        }
        for did in ids
    }

    # Prefer longer text as parent/canonical
    dup_edges: list[tuple[float, str, str]] = []  # sim, child, parent
    contain_edges: list[tuple[float, str, str]] = []  # cov, child, parent

    for (a, b), sc in pair_scores.items():
        sa_n, sb_n = sizes[a], sizes[b]
        ratio = (min(sa_n, sb_n) / max(sa_n, sb_n)) if max(sa_n, sb_n) else 0.0
        jac = sc["jaccard"]
        # Near-equal length + high Jaccard → duplicate edition
        if jac >= dup_threshold and ratio >= 0.55:
            if sa_n >= sb_n:
                dup_edges.append((jac, b, a))
            else:
                dup_edges.append((jac, a, b))
            continue
        # Shorter mostly inside longer → contained_in (even if Jaccard looks high on samples)
        if sc["cov_ab"] >= contain_threshold and sa_n < sb_n:
            contain_edges.append((sc["cov_ab"], a, b))
        if sc["cov_ba"] >= contain_threshold and sb_n < sa_n:
            contain_edges.append((sc["cov_ba"], b, a))
        # Similar-ish but not equal: still allow duplicate if Jaccard high
        elif jac >= dup_threshold and ratio >= 0.35:
            if sa_n >= sb_n:
                dup_edges.append((jac, b, a))
            else:
                dup_edges.append((jac, a, b))

    # Apply duplicates first (highest sim)
    dup_edges.sort(key=lambda t: (-t[0], t[1], t[2]))
    claimed: set[str] = set()
    for sim, child, parent in dup_edges:
        if child in claimed:
            continue
        if child == parent:
            continue
        # parent should remain canonical for now
        out[child] = {
            "canonical": False,
            "duplicate_of": parent,
            "contained_in": None,
            "sim": round(sim, 4),
            "coverage": 0.0,
            "relation": "duplicate",
        }
        claimed.add(child)

    contain_edges.sort(key=lambda t: (-t[0], t[1], t[2]))
    for cov, child, parent in contain_edges:
        if child in claimed:
            continue
        # do not contain into a duplicate child — resolve to its canonical parent if any
        parent_info = out.get(parent) or {}
        while parent_info.get("duplicate_of"):
            parent = parent_info["duplicate_of"]
            parent_info = out.get(parent) or {}
        if child == parent:
            continue
        out[child] = {
            "canonical": False,
            "duplicate_of": None,
            "contained_in": parent,
            "sim": 0.0,
            "coverage": round(cov, 4),
            "relation": "contained_in",
        }
        claimed.add(child)

    # Ensure duplicate_of targets that are themselves duplicates point to root
    for did, info in list(out.items()):
        parent = info.get("duplicate_of")
        hops = 0
        while parent and hops < 5:
            pinfo = out.get(parent) or {}
            if pinfo.get("duplicate_of"):
                parent = pinfo["duplicate_of"]
                hops += 1
                continue
            break
        if info.get("duplicate_of") and parent != info.get("duplicate_of"):
            info["duplicate_of"] = parent

    return out


def mark_canonical_duplicates(
    docs: list[Any],
    texts_by_id: dict[str, str],
    *,
    threshold: float = DUP_JACCARD,
) -> dict[str, dict[str, Any]]:
    """Back-compat wrapper: only duplicate/canonical (no contained_in promotion)."""
    full = detect_relations(docs, texts_by_id, dup_threshold=threshold)
    # For legacy callers: treat contained_in as canonical so old tests of
    # near-dup only still work when only dups are present.
    return full


def canonical_doc_ids(dup_map: dict[str, dict[str, Any]]) -> set[str]:
    """Docs that contribute to global DF/IDF (exclude duplicates and contained)."""
    return {
        did
        for did, info in dup_map.items()
        if info.get("canonical") and not info.get("contained_in") and not info.get("duplicate_of")
    }


def content_doc_ids(dup_map: dict[str, dict[str, Any]]) -> set[str]:
    """Canonical-only ids for textual layers (same as canonical_doc_ids)."""
    return canonical_doc_ids(dup_map)


def contains_edges_from_map(
    dup_map: dict[str, dict[str, Any]],
    *,
    edge_cls: Any,
) -> list[Any]:
    """Structural contains: container → contained doc."""
    edges: list[Any] = []
    for did, info in dup_map.items():
        parent = info.get("contained_in")
        if not parent:
            continue
        cov = float(info.get("coverage") or 0.0)
        edges.append(
            edge_cls(
                source=parent,
                target=did,
                type="contains",
                weight=round(cov, 4),
                provenance={
                    "relation": "contained_in",
                    "coverage": round(cov, 4),
                    "child": did,
                    "parent": parent,
                },
            )
        )
    return edges


def rewrite_refers_to_contains(
    edges: list[Any],
    dup_map: dict[str, dict[str, Any]],
    *,
    edge_cls: Any,
) -> list[Any]:
    """refers_to from container to its contained work/doc → contains."""
    contained_of: dict[str, str] = {
        did: info["contained_in"]
        for did, info in dup_map.items()
        if info.get("contained_in")
    }
    if not contained_of:
        return edges
    out: list[Any] = []
    for e in edges:
        if getattr(e, "type", None) != "refers_to":
            out.append(e)
            continue
        src, tgt = e.source, e.target
        # container refers to its nested doc
        if contained_of.get(tgt) == src:
            out.append(
                edge_cls(
                    source=src,
                    target=tgt,
                    type="contains",
                    weight=float(getattr(e, "weight", 1.0) or 1.0),
                    provenance={
                        **(getattr(e, "provenance", None) or {}),
                        "from_refers_to": True,
                        "relation": "contained_in",
                    },
                )
            )
            continue
        # nested refers to container — drop as refers, keep contains already
        if contained_of.get(src) == tgt:
            continue
        out.append(e)
    return out


def relation_stats(dup_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    dups = [
        {"doc": did, "duplicate_of": info.get("duplicate_of"), "sim": info.get("sim")}
        for did, info in dup_map.items()
        if info.get("relation") == "duplicate"
    ]
    contained = [
        {
            "doc": did,
            "contained_in": info.get("contained_in"),
            "coverage": info.get("coverage"),
        }
        for did, info in dup_map.items()
        if info.get("relation") == "contained_in"
    ]
    return {
        "canonical": sum(1 for info in dup_map.values() if info.get("canonical")),
        "duplicate": len(dups),
        "contained_in": len(contained),
        "pairs": dups[:40],
        "contained_pairs": contained[:40],
        "top_duplicate_examples": dups[:10],
        "top_contained_examples": contained[:10],
    }
