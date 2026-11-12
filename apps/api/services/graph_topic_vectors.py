"""Thematic (topic) vectors for clustering — style/idiolect downweighted.

Corpus-agnostic: author entropy, style stopwords, leave-one-out author debias.
No pymorphy/snowball in deps — suffix heuristics only. Offline, stdlib.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

from apps.api.services import graph_lexicon as glex

# Noun-ish suffixes → thematic weight ×1.5 (ru / en).
_RU_NOUN_SFX = (
    "ость",
    "есть",
    "ение",
    "ание",
    "ство",
    "ствие",
    "ция",
    "сия",
    "тор",
    "тель",
    "ница",
    "ник",
    "щик",
    "изм",
    "ура",
    "ика",
)
_EN_NOUN_SFX = ("tion", "sion", "ment", "ness", "ity", "ance", "ence", "ship", "hood", "ism", "ure")

# Gerund / archaic participles — drop for ru topic vectors.
_RU_GERUND = re.compile(
    r"(вши|вшись|учи|ючи|яся|ась|ясь)$",
    re.I,
)


def style_stopwords(lang: str = "ru") -> frozenset[str]:
    """Explicit style / speech particles for topic vectors and labels."""
    block = glex.lang_block(lang)
    nested = block.get("topic_style_stopwords") or []
    # Also allow top-level topic_style_stopwords.{ru,en}
    root = glex.load_graph_lexicon().get("topic_style_stopwords") or {}
    extra: list[str] = []
    if isinstance(root, dict):
        extra = list(root.get(lang) or [])
    elif isinstance(nested, list):
        extra = []
    words = list(nested) + list(extra)
    # Fall back: reuse speech_verbs + play_stage + style_burst hints lightly
    if not words:
        words = list(glex.lang_frozenset(lang, "speech_verbs") | glex.lang_frozenset(lang, "play_stage_words"))
    return frozenset(str(w).casefold().replace("ё", "е") for w in words if w)


def is_ru_gerund_or_archaic(stem: str) -> bool:
    s = (stem or "").casefold().replace("ё", "е")
    if len(s) < 5:
        return False
    return bool(_RU_GERUND.search(s))


def noun_boost(stem: str, lang: str) -> float:
    s = (stem or "").casefold()
    if lang == "en":
        return 1.5 if any(s.endswith(sfx) for sfx in _EN_NOUN_SFX) else 1.0
    return 1.5 if any(s.endswith(sfx) for sfx in _RU_NOUN_SFX) else 1.0


def _source_group(work: dict[str, Any]) -> str | None:
    ak = work.get("author_key")
    if ak:
        return f"author:{ak}"
    col = work.get("collection") or work.get("theme")
    if col:
        return f"collection:{col}"
    pack = work.get("pack") or work.get("source")
    if pack:
        return f"pack:{pack}"
    fn = str(work.get("filename") or "")
    if "_" in fn:
        return f"prefix:{fn.split('_', 1)[0]}"
    return None


def author_term_entropy(
    work_terms: dict[str, Counter],
    works: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Per-term author (or source_group) distribution + normalized entropy.

    Returns stem → {entropy, n_authors, df, weight_mult}.
    """
    # stem → group → work-df
    by_stem: dict[str, Counter] = defaultdict(Counter)
    group_of: dict[str, str | None] = {}
    for wk, info in works.items():
        group_of[wk] = _source_group(info)
    for wk, counts in work_terms.items():
        g = group_of.get(wk)
        for stem, tf in counts.items():
            if int(tf) <= 0:
                continue
            key = g if g is not None else f"work:{wk}"
            by_stem[str(stem)][key] += 1

    # Groups that exist in corpus (for entropy normalization)
    all_groups = {g for g in group_of.values() if g}
    n_groups_corpus = max(1, len(all_groups))
    # If almost no groups (all unknown single works), skip filter later.
    out: dict[str, dict[str, Any]] = {}
    for stem, dist in by_stem.items():
        n = sum(dist.values())
        if n <= 0:
            continue
        ent = 0.0
        for c in dist.values():
            p = c / n
            if p > 0:
                ent -= p * math.log(p + 1e-12)
        # Normalize by log(|support|) so 0..1
        support = max(1, len(dist))
        norm = ent / math.log(support) if support > 1 else 0.0
        out[stem] = {
            "entropy": round(norm, 4),
            "n_authors": len(dist),
            "df": n,
            "dist": dict(dist),
        }
    return out


def detect_style_stems(
    work_terms: dict[str, Counter],
    works: dict[str, dict[str, Any]],
    *,
    single_author_share: float = 0.60,
    top_author_pct: float = 0.01,
    min_author_vocab: int = 80,
    min_author_works: int = 8,
) -> set[str]:
    """Idiolect markers: >60% work-df in one author, top-1% tf, large author vocab.

    Small synthetic corpora and thin authors are skipped — otherwise every
    single-author theme word would be wiped as «style».
    """
    group_of = {wk: _source_group(info) for wk, info in works.items()}
    works_per_group: Counter[str] = Counter()
    for g in group_of.values():
        if g and str(g).startswith("author:"):
            works_per_group[str(g)] += 1
    group_tf: dict[str, Counter] = defaultdict(Counter)
    stem_df: dict[str, Counter] = defaultdict(Counter)
    for wk, counts in work_terms.items():
        g = group_of.get(wk)
        if not g or not str(g).startswith("author:"):
            continue
        for stem, tf in counts.items():
            if int(tf) <= 0:
                continue
            group_tf[str(g)][str(stem)] += int(tf)
            stem_df[str(stem)][str(g)] += 1
    style: set[str] = set()
    for stem, dist in stem_df.items():
        total = sum(dist.values())
        if total <= 0:
            continue
        top_g, top_n = dist.most_common(1)[0]
        if top_n / total < single_author_share:
            continue
        if works_per_group.get(top_g, 0) < min_author_works:
            continue
        tf_map = group_tf.get(top_g) or Counter()
        if len(tf_map) < min_author_vocab:
            continue
        ranked = sorted(tf_map.values(), reverse=True)
        cutoff_idx = max(0, int(len(ranked) * top_author_pct) - 1)
        thr = ranked[cutoff_idx] if ranked else 0
        if tf_map.get(stem, 0) >= thr and thr > 0:
            style.add(stem)
    return style


def thematic_weight(
    stem: str,
    *,
    lang: str,
    entropy_info: dict[str, Any] | None,
    style_stops: frozenset[str],
    auto_style: set[str],
    min_author_entropy: float,
    min_authors: int,
    apply_entropy: bool,
) -> float:
    """Weight multiplier for a stem in topic vectors (0 = drop, else ≥0.1)."""
    s = (stem or "").casefold().replace("ё", "е")
    if len(s) < 4:
        return 0.0
    if s in style_stops or s in auto_style:
        return 0.0
    if lang.startswith("ru") and is_ru_gerund_or_archaic(s):
        return 0.0
    w = 1.0
    if apply_entropy and entropy_info is not None:
        n_auth = int(entropy_info.get("n_authors") or 0)
        ent = float(entropy_info.get("entropy") or 0)
        if n_auth < min_authors or ent < min_author_entropy:
            w *= 0.1
    w *= noun_boost(s, "en" if lang.startswith("en") else "ru")
    return w


def build_raw_vectors(
    work_terms: dict[str, Counter],
    works: dict[str, dict[str, Any]],
    *,
    params: dict[str, Any],
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """TF-log vectors with thematic filters. Returns vectors + debug stats."""
    min_ent = float(params.get("min_author_entropy") or 0.5)
    min_authors = int(params.get("min_topic_authors") or 3)
    term_cap = int(params.get("topic_term_cap") or 2000)
    pre_top = int(params.get("topic_raw_top_k") or 800)
    # Cap hapax-heavy novel bags before entropy/style/weighting.
    capped: dict[str, Counter] = {}
    for wk, counts in work_terms.items():
        if term_cap > 0 and len(counts) > term_cap:
            capped[wk] = Counter(dict(counts.most_common(term_cap)))
        else:
            capped[wk] = counts
    entropy = author_term_entropy(capped, works)
    # Apply entropy only when ≥3 distinct author groups exist in corpus
    groups = {_source_group(w) for w in works.values()}
    author_groups = {g for g in groups if g and str(g).startswith("author:")}
    apply_entropy = len(author_groups) >= min_authors
    style_by_lang = {
        "ru": style_stopwords("ru"),
        "en": style_stopwords("en"),
    }
    auto_style = detect_style_stems(capped, works)
    low_entropy: list[tuple[float, str, int]] = []
    for stem, info in entropy.items():
        low_entropy.append((float(info["entropy"]), stem, int(info["n_authors"])))
    low_entropy.sort()

    vectors: dict[str, dict[str, float]] = {}
    kept = 0
    dropped_style = 0
    for wk, counts in capped.items():
        lang = str((works.get(wk) or {}).get("language") or "ru")
        stops = style_by_lang["en"] if lang.startswith("en") else style_by_lang["ru"]
        vec: dict[str, float] = {}
        for stem, tf in counts.items():
            info = entropy.get(str(stem))
            mult = thematic_weight(
                str(stem),
                lang=lang,
                entropy_info=info,
                style_stops=stops,
                auto_style=auto_style,
                min_author_entropy=min_ent,
                min_authors=min_authors,
                apply_entropy=apply_entropy,
            )
            if mult <= 0:
                dropped_style += 1
                continue
            w = (1.0 + math.log(1.0 + float(tf))) * mult
            if w > 0:
                vec[str(stem)] = w
                kept += 1
        if pre_top > 0 and len(vec) > pre_top:
            vec = dict(sorted(vec.items(), key=lambda x: -x[1])[:pre_top])
        vectors[wk] = vec
    debug = {
        "apply_entropy": apply_entropy,
        "n_author_groups": len(author_groups),
        "auto_style_stems": sorted(auto_style)[:40],
        "low_entropy_top20": [
            {"stem": s, "entropy": e, "n_authors": n} for e, s, n in low_entropy[:20]
        ],
        "kept_weights": kept,
        "dropped_style_hits": dropped_style,
        "style_stop_ru_n": len(style_by_lang["ru"]),
        "topic_term_cap": term_cap,
        "topic_raw_top_k": pre_top,
    }
    return vectors, debug


def _normalize(vec: dict[str, float]) -> dict[str, float]:
    n = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / n for k, v in vec.items()}


def author_debias(
    vectors: dict[str, dict[str, float]],
    works: dict[str, dict[str, Any]],
    *,
    alpha: float,
) -> dict[str, dict[str, float]]:
    """v' = normalize(v − α · mean(other works of same author)).

    Leave-one-out mean is computed from a pre-summed author total so cost is
    O(|works| · |vocab|) instead of O(n² · vocab) for prolific authors.
    """
    by_author: dict[str, list[str]] = defaultdict(list)
    for wk, info in works.items():
        ak = info.get("author_key")
        if ak and wk in vectors:
            by_author[str(ak)].append(wk)
    author_sum: dict[str, dict[str, float]] = {}
    for ak, wks in by_author.items():
        total: dict[str, float] = {}
        for wk in wks:
            for stem, val in vectors[wk].items():
                total[stem] = total.get(stem, 0.0) + float(val)
        author_sum[ak] = total
    out: dict[str, dict[str, float]] = {}
    for wk, vec in vectors.items():
        ak = (works.get(wk) or {}).get("author_key")
        peers = by_author.get(str(ak) or "", [])
        if not ak or len(peers) < 2 or alpha <= 0:
            out[wk] = _normalize(vec)
            continue
        total = author_sum[str(ak)]
        inv = 1.0 / (len(peers) - 1)
        deb: dict[str, float] = {}
        # Keys in the author total cover every peer (incl. self).
        for stem, tval in total.items():
            self_v = float(vec.get(stem) or 0.0)
            mean_others = (float(tval) - self_v) * inv
            val = self_v - alpha * mean_others
            if abs(val) > 1e-9:
                deb[stem] = val
        out[wk] = _normalize(deb) if deb else _normalize(vec)
    return out


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b[k] for k, v in a.items() if k in b)


def knn_edges(
    vectors: dict[str, dict[str, float]],
    works: dict[str, dict[str, Any]],
    *,
    k: int = 10,
    cross_author_min: int = 3,
    cross_author_min_sim: float = 0.15,
    weak_floor: float = 0.08,
) -> list[dict[str, Any]]:
    """Mutual kNN on topic vectors with cross-author quota.

    Returns edge dicts type=topic_knn with weight=cosine, provenance.terms.
    """
    keys = sorted(wk for wk in vectors if vectors[wk] and wk in works)
    # Precompute top-k per node
    nbrs: dict[str, list[tuple[str, float]]] = {}
    for i, a in enumerate(keys):
        scored: list[tuple[str, float]] = []
        va = vectors[a]
        for b in keys:
            if a == b:
                continue
            s = cosine(va, vectors[b])
            if s > 0:
                scored.append((b, s))
        scored.sort(key=lambda x: (-x[1], x[0]))
        top = scored[:k]
        # Cross-author quota: ensure ≥ cross_author_min other-author neighbors when possible
        ak = works[a].get("author_key")
        if ak and cross_author_min > 0:
            have = sum(1 for b, s in top if works[b].get("author_key") and works[b].get("author_key") != ak and s >= cross_author_min_sim)
            if have < cross_author_min:
                extras = [
                    (b, s)
                    for b, s in scored
                    if works[b].get("author_key")
                    and works[b].get("author_key") != ak
                    and s >= cross_author_min_sim
                    and (b, s) not in top
                ]
                need = cross_author_min - have
                # replace weakest same-author slots
                same_slots = [(b, s) for b, s in top if works[b].get("author_key") == ak]
                same_slots.sort(key=lambda x: x[1])  # weakest first
                replace = min(need, len(same_slots), len(extras))
                if replace:
                    drop = {b for b, _ in same_slots[:replace]}
                    top = [(b, s) for b, s in top if b not in drop] + extras[:replace]
                    top.sort(key=lambda x: (-x[1], x[0]))
                    top = top[:k]
        nbrs[a] = top

    # Mutual + weak fallback
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    for a, lst in nbrs.items():
        for b, s in lst:
            mutual = any(x == a for x, _ in nbrs.get(b) or [])
            weak = not mutual
            if weak and s < weak_floor:
                continue
            wa, wb = (a, b) if a < b else (b, a)
            key = (wa, wb)
            shared = sorted(set(vectors[a]) & set(vectors[b]), key=lambda t: -(vectors[a][t] * vectors[b].get(t, 0)))[:8]
            prev = edges.get(key)
            if prev is None or s > float(prev["weight"]):
                edges[key] = {
                    "source": f"work:{wa}",
                    "target": f"work:{wb}",
                    "type": "topic_knn",
                    "weight": round(float(s), 6),
                    "weak": weak,
                    "provenance": {
                        "terms": shared,
                        "sim": round(float(s), 6),
                        "mutual": mutual,
                        "level": "work",
                    },
                }
    return sorted(edges.values(), key=lambda e: (e["source"], e["target"]))


def segment_top_terms(
    work_terms: dict[str, Counter],
    works: dict[str, dict[str, Any]],
    *,
    chunk_threshold: int = 20,
    top_n: int = 3,
) -> dict[str, list[str]]:
    """For large works, keep top-N thematic stems as soft-membership hints.

    Without per-segment artifacts we approximate by taking the work's own top
    thematic stems (already filtered vectors preferred when passed).
    """
    out: dict[str, list[str]] = {}
    for wk, info in works.items():
        n_chunks = int(info.get("chunk_count") or info.get("fragment_count") or 0)
        # Also treat multi-doc works as "large"
        n_docs = len(info.get("document_ids") or [])
        if n_chunks < chunk_threshold and n_docs < 3:
            continue
        counts = work_terms.get(wk) or Counter()
        tops = [s for s, _ in counts.most_common(top_n * 3) if len(str(s)) >= 4][:top_n]
        if tops:
            out[wk] = tops
    return out


def prune_vector(vec: dict[str, float], top_k: int) -> dict[str, float]:
    """Keep strongest dims and re-normalize (speeds knn on novel-sized bags)."""
    if top_k <= 0 or len(vec) <= top_k:
        return vec
    top = sorted(vec.items(), key=lambda x: -abs(x[1]))[:top_k]
    return _normalize(dict(top))


def build_topic_vectors(
    work_terms: dict[str, Counter],
    works: dict[str, dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Full pipeline: filter → debias → normalize. Debug included."""
    raw, debug = build_raw_vectors(work_terms, works, params=params)
    alpha = float(params.get("author_debias_alpha") or 0.6)
    vectors = author_debias(raw, works, alpha=alpha)
    top_k = int(params.get("topic_vector_top_k") or 400)
    if top_k > 0:
        vectors = {wk: prune_vector(vec, top_k) for wk, vec in vectors.items()}
        debug = dict(debug)
        debug["topic_vector_top_k"] = top_k
    segments = segment_top_terms(
        work_terms,
        works,
        chunk_threshold=int(params.get("segment_chunk_threshold") or 20),
        top_n=int(params.get("segment_top_terms") or 3),
    )
    return {
        "vectors": vectors,
        "debug": debug,
        "segment_terms": segments,
        "alpha": alpha,
    }
