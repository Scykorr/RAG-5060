"""Slice 2c: proper-noun helpers, weak-work routing, grounded labels, vector cache."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from apps.api.services import graph_index_cache as gcache
from apps.api.services import graph_topic_labels as labels
from apps.api.services import graph_topic_vectors as gtv

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{3,}")


_PERSON_PLACE_KINDS = frozenset(
    {"person", "character", "place", "name", "anthroponym", "toponym", ""}
)


def entity_stems_from_index(index: dict[str, Any]) -> set[str]:
    """Person/place stems from shared_entity (ban in topic vectors, keep in entity layer)."""
    out: set[str] = set()
    for edge in index.get("edges") or []:
        typ = str(edge.get("type") or "")
        if typ not in {"shared_entity", "work_shared_entity"}:
            continue
        prov = edge.get("provenance") or {}
        blobs: list[Any] = [prov]
        for ex in prov.get("examples") or []:
            if isinstance(ex, dict):
                blobs.append(ex.get("provenance") or {})
        for blob in blobs:
            if not isinstance(blob, dict):
                continue
            kinds = blob.get("entity_kinds") or {}
            if not isinstance(kinds, dict):
                kinds = {}
            for ent in blob.get("entities") or []:
                text = str(ent or "").casefold().replace("ё", "е").strip()
                if len(text) < 4 or " " in text or "-" in text:
                    # Multi-word titles / «иван иваныч» — ban first token only if person-like.
                    parts = [p for p in text.replace("-", " ").split() if len(p) >= 4]
                    text = parts[0] if parts else ""
                    if not text:
                        continue
                kind = str(kinds.get(ent) or kinds.get(str(ent)) or "").casefold()
                if kind and kind not in _PERSON_PLACE_KINDS:
                    continue
                # Skip obvious non-name leftovers (adjectives / common stems).
                if text.endswith(("ск", "ость", "ени", "ения")) and kind not in {
                    "person",
                    "character",
                    "name",
                    "anthroponym",
                }:
                    continue
                if any("а" <= ch <= "я" for ch in text):
                    from apps.api.services.text_entities import stem_ru

                    out.add(stem_ru(text))
                else:
                    out.add(text)
    return out


def work_token_estimate(counts: Counter | None) -> int:
    if not counts:
        return 0
    try:
        return int(sum(int(v) for v in counts.values()))
    except (TypeError, ValueError):
        return 0


def topic_confidence(
    wk: str,
    members: set[str],
    adj: dict[str, dict[str, float]],
) -> float:
    """Share of edge weight from work that stays inside its topic."""
    nbrs = adj.get(wk) or {}
    if not nbrs:
        return 0.0
    total = sum(float(w) for w in nbrs.values()) or 1.0
    inside = sum(float(w) for nb, w in nbrs.items() if nb in members and nb != wk)
    return inside / total


def vector_cache_dir(tenant_id: UUID | str) -> Path:
    path = gcache.graph_dir() / "topics" / "vec_cache" / str(tenant_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_vector_cache(tenant_id: UUID | str, filter_hash: str) -> dict[str, Any]:
    path = vector_cache_dir(tenant_id) / f"{filter_hash}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_vector_cache(tenant_id: UUID | str, filter_hash: str, payload: dict[str, Any]) -> None:
    path = vector_cache_dir(tenant_id) / f"{filter_hash}.json"
    gcache.write_json_atomic(path, payload, indent=None)


def try_cached_topic_vectors(
    *,
    tenant_id: UUID | str,
    works: dict[str, dict[str, Any]],
    work_hashes: dict[str, str],
    params: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return built vectors from cache when every work hash matches, else None.

    Partial hits are reported in timing; caller must rebuild on ``None``.
    """
    fch = gtv.filter_config_hash(params)
    cached = load_vector_cache(tenant_id, fch)
    timing = {"filter_config_hash": fch, "cache_hits": 0, "cache_misses": 0}
    if not cached or not works:
        timing["cache_misses"] = len(works)
        return None, timing
    vectors: dict[str, dict[str, float]] = {}
    segments: dict[str, list[str]] = {}
    for wk in works:
        ch = str(work_hashes.get(wk) or "")
        row = cached.get(wk) or {}
        if not ch or row.get("content_hash") != ch or not isinstance(row.get("vector"), dict):
            timing["cache_misses"] += 1
            continue
        vectors[wk] = dict(row["vector"])
        if row.get("segment_terms"):
            segments[wk] = list(row["segment_terms"])
        timing["cache_hits"] += 1
    if timing["cache_misses"] or len(vectors) != len(works):
        timing["cache_misses"] = max(timing["cache_misses"], len(works) - len(vectors))
        return None, timing
    debug = cached.get("_debug") if isinstance(cached.get("_debug"), dict) else {}
    return (
        {
            "vectors": vectors,
            "debug": dict(debug or {}),
            "segment_terms": segments,
            "alpha": float(params.get("author_debias_alpha") or 0.6),
        },
        timing,
    )


def store_topic_vectors_cache(
    *,
    tenant_id: UUID | str,
    work_hashes: dict[str, str],
    params: dict[str, Any],
    built: dict[str, Any],
) -> None:
    fch = gtv.filter_config_hash(params)
    store: dict[str, Any] = {"_debug": built.get("debug") or {}}
    for wk, vec in (built.get("vectors") or {}).items():
        store[wk] = {
            "content_hash": str(work_hashes.get(wk) or ""),
            "vector": vec,
            "segment_terms": (built.get("segment_terms") or {}).get(wk) or [],
        }
    try:
        save_vector_cache(tenant_id, fch, store)
    except OSError:
        pass


def label_grounded(label: str, terms: list[str], *, min_hits: int = 2) -> tuple[bool, list[str]]:
    from apps.api.services.text_entities import stem_ru

    term_bag: set[str] = set()
    for t in terms:
        raw = str(t or "").casefold().replace("ё", "е")
        if not raw:
            continue
        term_bag.add(raw)
        if any("а" <= ch <= "я" for ch in raw):
            term_bag.add(stem_ru(raw))
    hits: list[str] = []
    for m in _WORD_RE.finditer(label or ""):
        w = m.group(0).casefold().replace("ё", "е")
        stem = stem_ru(w) if any("а" <= ch <= "я" for ch in w) else w
        matched = w in term_bag or stem in term_bag
        if not matched:
            # Prefix soft-match: «кавказская» ↔ «кавказ»
            for t in term_bag:
                if len(t) >= 4 and (w.startswith(t) or t.startswith(w) or stem.startswith(t) or t.startswith(stem)):
                    matched = True
                    break
        if matched:
            hits.append(w)
    return len(set(hits)) >= min_hits, hits


def _ollama_short(prompt: str, model: str, generate: Callable[[str, str], str] | None) -> str | None:
    if generate is not None:
        try:
            raw = generate(prompt, model) or ""
        except Exception:
            return None
    else:
        try:
            import httpx

            from apps.api.services import ollama as oll

            host = oll._host()  # noqa: SLF001
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"num_predict": 48, "temperature": 0.2},
            }
            with httpx.Client(timeout=90.0) as client:
                r = client.post(f"{host}/api/generate", json=payload)
                r.raise_for_status()
                data = r.json() if r.content else {}
                raw = str(data.get("response") or data.get("thinking") or "")
        except Exception:
            return None
    for line in str(raw).splitlines():
        cleaned = line.strip().strip(" \t\"'«»“”„#-")
        if not cleaned:
            continue
        if cleaned.lower().startswith(("think", "thinking", "рассужд")):
            continue
        return " ".join(cleaned.split()[:5])[:120]
    return None


def maybe_llm_label_grounded(
    *,
    enabled: bool,
    model: str,
    terms: list[str],
    titles: list[str],
    lang: str,
    cache_key: str,
    cache: dict[str, str],
    generate: Callable[[str, str], str] | None = None,
    topic_labels: list[str] | None = None,
    for_theme: bool = False,
) -> dict[str, Any]:
    """LLM label with grounded check + one retry; else term fallback."""
    term_label = labels.default_label(terms, titles[0] if titles else None, n_terms=3)
    if not enabled:
        return {
            "label": term_label,
            "label_source": "terms",
            "llm_label": None,
            "label_grounded": True,
            "ground_terms": list(terms[:3]),
            "term_label": term_label,
        }
    cached = cache.get(cache_key)
    if cached:
        ok, hits = label_grounded(cached, terms)
        if ok:
            return {
                "label": cached,
                "label_source": "llm",
                "llm_label": cached,
                "label_grounded": True,
                "ground_terms": hits,
                "term_label": term_label,
            }
    lang_name = {"ru": "русском", "en": "английском"}.get(lang or "", "языке большинства документов")
    kind = "темы (группы тематик)" if for_theme else "тематики"
    extra = ""
    if topic_labels:
        extra = f"Подписи входящих тематик: {', '.join(topic_labels[:12])}\n"
    prompt = (
        f"Дай короткое название {kind} (2–5 слов) на {lang_name}. "
        "Это предмет корпуса, не стиль и не имя автора. "
        "Не придумывай жанр или профессию, если её нет в терминах; "
        "лучше общее название, чем неверное конкретное. "
        "Примеры: «Кавказ и война», «Крестьянская жизнь», «Религия и непротивление». "
        "Без кавычек, без имён авторов, только название.\n"
        f"{extra}"
        f"Характерные термины: {', '.join(terms[:10]) or '—'}\n"
        f"Центральные произведения: {'; '.join(titles[:5]) or '—'}\n"
    )
    llm = _ollama_short(prompt, model, generate)
    if not llm:
        return {
            "label": term_label,
            "label_source": "terms",
            "llm_label": None,
            "label_grounded": True,
            "ground_terms": list(terms[:3]),
            "term_label": term_label,
        }
    ok, hits = label_grounded(llm, terms)
    if not ok:
        retry = prompt + f"\nИспользуй обязательно слова из списка: {', '.join(terms[:12])}.\n"
        llm2 = _ollama_short(retry, model, generate)
        if llm2:
            llm = llm2
            ok, hits = label_grounded(llm, terms)
    if ok and llm:
        cache[cache_key] = llm
        return {
            "label": llm,
            "label_source": "llm",
            "llm_label": llm,
            "label_grounded": True,
            "ground_terms": hits,
            "term_label": term_label,
        }
    return {
        "label": term_label,
        "label_source": "terms_fallback",
        "llm_label": llm,
        "label_grounded": False,
        "ground_terms": list(terms[:3]),
        "term_label": term_label,
    }


def theme_affinity_stats(payload: dict[str, Any]) -> dict[str, Any]:
    """Intra/inter theme affinity on 0–1 scale: mean edge weight per work-pair."""
    themes = [
        t
        for t in (payload.get("themes") or [])
        if str(t.get("collection") or "") != "Прочее" and int(t.get("size") or 0) > 0
    ]
    assign = payload.get("assignments") or {}
    works = payload.get("works") or {}
    size = {str(t["id"]): max(1, int(t.get("size") or 1)) for t in themes}
    ids = set(size)
    # Per-theme sum of internal edge weights
    intra_sum: dict[str, float] = {tid: 0.0 for tid in ids}
    cross_sum: dict[tuple[str, str], float] = {}
    for edge in payload.get("work_edges") or []:
        wa = str(edge["source"])[5:]
        wb = str(edge["target"])[5:]
        ta = (assign.get(wa) or {}).get("theme_id")
        tb = (assign.get(wb) or {}).get("theme_id")
        if not ta or not tb:
            continue
        w = float(edge.get("weight") or 0)
        if ta == tb and ta in ids:
            intra_sum[ta] = intra_sum.get(ta, 0.0) + w
        elif ta in ids and tb in ids:
            a, b = (ta, tb) if ta < tb else (tb, ta)
            cross_sum[(a, b)] = cross_sum.get((a, b), 0.0) + w
    intra_vals = []
    for tid, raw in intra_sum.items():
        n = size[tid]
        pairs = max(1, n * (n - 1) / 2)
        intra_vals.append(raw / pairs)
    inter_vals = []
    for (a, b), raw in cross_sum.items():
        pairs = max(1.0, float(size[a] * size[b]))
        inter_vals.append(raw / pairs)
    mean_intra = sum(intra_vals) / len(intra_vals) if intra_vals else 0.0
    mean_inter = sum(inter_vals) / len(inter_vals) if inter_vals else 0.0
    max_inter = max(inter_vals) if inter_vals else 0.0
    ratio = mean_intra / mean_inter if mean_inter > 1e-12 else 99.0
    _ = works  # reserved for future per-work diagnostics
    return {
        "mean_intra": round(mean_intra, 4),
        "mean_inter": round(mean_inter, 4),
        "max_inter": round(max_inter, 4),
        "ratio": round(ratio, 4),
        "n_themes": len(ids),
    }


def theme_link_stats(
    themes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if payload is not None:
        return theme_affinity_stats(payload)
    ids = [str(t["id"]) for t in themes if str(t.get("collection") or "") != "Прочее"]
    inter_vals = []
    for edge in links:
        a, b = str(edge.get("source")), str(edge.get("target"))
        if a in ids and b in ids and a != b:
            inter_vals.append(float(edge.get("weight") or 0))
    max_inter = max(inter_vals) if inter_vals else 0.0
    mean_inter = sum(inter_vals) / len(inter_vals) if inter_vals else 0.0
    dens = [float(t.get("density") or 0) for t in themes if str(t.get("collection") or "") != "Прочее"]
    mean_intra = sum(dens) / len(dens) if dens else max(0.0, 1.0 - mean_inter)
    ratio = mean_intra / mean_inter if mean_inter > 1e-9 else 99.0
    return {
        "mean_intra": round(mean_intra, 4),
        "mean_inter": round(mean_inter, 4),
        "max_inter": round(max_inter, 4),
        "ratio": round(ratio, 4),
        "n_themes": len(ids),
    }


def sweep_theme_resolution(
    index: dict[str, Any],
    base_params: dict[str, Any],
    *,
    resolutions: list[float] | None = None,
    work_terms: dict[str, Any] | None = None,
    case_stats: dict[str, dict[str, int]] | None = None,
    pos_stats: dict[str, dict[str, int]] | None = None,
    tenant_id: str = "",
) -> list[dict[str, Any]]:
    """Grid search theme_resolution; metric = mean_intra / mean_inter."""
    from apps.api.services import graph_topics as gt

    grid = resolutions or [1.0, 1.3, 1.6, 2.0]
    rows: list[dict[str, Any]] = []
    for res in grid:
        params = dict(base_params)
        params["theme_resolution"] = float(res)
        params["llm"] = False
        payload = gt.build_topics_from_index(
            index,
            params=params,
            overrides={"topics": {}, "themes": {}, "merges": [], "splits": []},
            previous=None,
            work_terms=work_terms,
            case_stats=case_stats,
            pos_stats=pos_stats,
            force_mode="full",
            llm_generate=lambda _p, _m: "",
            tenant_id=tenant_id,
        )
        stats = theme_affinity_stats(payload)
        n_topics = int((payload.get("stats") or {}).get("topics") or 0)
        ok = (
            stats["max_inter"] <= 0.2
            and 4 <= stats["n_themes"] <= 10
            and 15 <= n_topics <= 60
        )
        rows.append(
            {
                "theme_resolution": res,
                **stats,
                "n_topics": n_topics,
                "ok": ok,
            }
        )
    # Prefer ok rows with highest ratio; else highest ratio under theme count soft bound.
    rows.sort(
        key=lambda r: (
            0 if r["ok"] else 1,
            -float(r["ratio"]),
            abs(float(r["n_themes"]) - 7),
        )
    )
    return rows
