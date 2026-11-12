"""Slice 2c/2d: proper-noun helpers, weak-work routing, grounded labels, vector cache."""

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
_GLUE_RE = re.compile(
    r"^\s*([A-Za-zА-Яа-яЁё\-]{3,})\s+и\s+([A-Za-zА-Яа-яЁё\-]{3,})\s*$",
    flags=re.UNICODE | re.IGNORECASE,
)

_PERSON_PLACE_KINDS = frozenset(
    {"person", "character", "place", "name", "anthroponym", "toponym", ""}
)

_STOP_LABEL = frozenset(
    {
        "и",
        "или",
        "the",
        "a",
        "an",
        "of",
        "and",
        "или",
        "про",
        "для",
        "без",
        "при",
        "над",
        "под",
    }
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
                    parts = [p for p in text.replace("-", " ").split() if len(p) >= 4]
                    text = parts[0] if parts else ""
                    if not text:
                        continue
                kind = str(kinds.get(ent) or kinds.get(str(ent)) or "").casefold()
                if kind and kind not in _PERSON_PLACE_KINDS:
                    continue
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


def _stem_bag(words: list[str]) -> set[str]:
    from apps.api.services.text_entities import stem_ru

    bag: set[str] = set()
    for t in words:
        raw = str(t or "").casefold().replace("ё", "е").strip()
        if not raw:
            continue
        bag.add(raw)
        if any("а" <= ch <= "я" for ch in raw):
            bag.add(stem_ru(raw))
    return bag


def label_is_top6_glue(label: str, top_terms: list[str], *, top_n: int = 6) -> bool:
    """Reject two-word «A и B» glues (esp. when both sides are from top-N terms)."""
    m = _GLUE_RE.match((label or "").strip())
    if not m:
        return False
    # Slice 2d: never accept two-word «A и B» LLM titles (top-N or otherwise).
    _ = top_terms, top_n
    return True


def label_grounded(
    label: str,
    terms: list[str],
    *,
    fragment_lexicon: list[str] | None = None,
    min_hits: int = 1,
) -> tuple[bool, list[str]]:
    """True when ≥1 content word of the label matches top terms or fragment stems."""
    from apps.api.services.text_entities import stem_ru

    term_bag = _stem_bag(list(terms) + list(fragment_lexicon or []))
    hits: list[str] = []
    for m in _WORD_RE.finditer(label or ""):
        w = m.group(0).casefold().replace("ё", "е")
        if w in _STOP_LABEL:
            continue
        stem = stem_ru(w) if any("а" <= ch <= "я" for ch in w) else w
        matched = w in term_bag or stem in term_bag
        if not matched:
            for t in term_bag:
                if len(t) >= 4 and (
                    w.startswith(t) or t.startswith(w) or stem.startswith(t) or t.startswith(stem)
                ):
                    matched = True
                    break
        if matched:
            hits.append(w)
    return len(set(hits)) >= min_hits, hits


def _parse_llm_label(raw: str | None) -> str | None:
    if not raw:
        return None
    for line in str(raw).splitlines():
        cleaned = line.strip().strip(" \t\"'«»“”„#-")
        if not cleaned:
            continue
        if cleaned.lower().startswith(("think", "thinking", "рассужд")):
            continue
        upper = cleaned.upper()
        if upper in {"NONE", "НЕТ", "N/A", "-"}:
            return None
        return " ".join(cleaned.split()[:5])[:120]
    return None


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
    return _parse_llm_label(raw)


def _label_acceptable(
    llm: str,
    *,
    terms: list[str],
    fragment_lexicon: list[str] | None,
    forbidden_labels: list[str] | None,
) -> tuple[bool, list[str], str | None]:
    """Return (ok, ground_hits, reject_reason)."""
    if not llm or not llm.strip():
        return False, [], "empty"
    if llm.strip().upper() == "NONE":
        return False, [], "none"
    if label_is_top6_glue(llm, terms, top_n=6):
        return False, [], "glue_top6"
    if forbidden_labels:
        norm = llm.casefold().replace("ё", "е").strip()
        for bad in forbidden_labels:
            if norm == str(bad or "").casefold().replace("ё", "е").strip():
                return False, [], "duplicate_theme_topic"
    ok, hits = label_grounded(llm, terms[:30], fragment_lexicon=fragment_lexicon, min_hits=1)
    if not ok:
        return False, hits, "ungrounded"
    return True, hits, None


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
    snippets: list[str] | None = None,
    forbidden_labels: list[str] | None = None,
) -> dict[str, Any]:
    """LLM label with stem grounding + glue reject + one retry; else 3-term fallback."""
    term_label = labels.default_label(terms, None, n_terms=3, with_title=False)
    fragment_lex = list(snippets or [])
    forbid = list(forbidden_labels or [])
    if for_theme and topic_labels:
        forbid.extend(topic_labels)

    def _fallback(llm_raw: str | None, reason: str | None = None) -> dict[str, Any]:
        return {
            "label": term_label,
            "label_source": "terms_fallback" if enabled else "terms",
            "llm_label": llm_raw,
            "label_grounded": False if enabled else True,
            "ground_terms": list(terms[:3]),
            "term_label": term_label,
            "reject_reason": reason,
        }

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
        ok, hits, reason = _label_acceptable(
            cached, terms=terms, fragment_lexicon=fragment_lex, forbidden_labels=forbid
        )
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
    works_line = "; ".join(titles[:5]) if titles else "—"
    snip_line = " | ".join(s.strip()[:160] for s in fragment_lex[:3] if s and str(s).strip()) or "—"
    forbid_line = ""
    if forbid:
        forbid_line = f"Не используй эти названия (уже заняты): {', '.join(forbid[:12])}.\n"
    prompt = (
        f"Сформулируй тему содержания {kind} на {lang_name}: 2–5 слов, "
        "именительный падеж. Это предмет корпуса, не стиль. "
        "Без имён персонажей, без авторов и без названий произведений. "
        "Не склеивай два термина через «и». "
        "Если связать нечем — верни ровно NONE.\n"
        f"{forbid_line}"
        f"Топ-термины: {', '.join(terms[:15]) or '—'}\n"
        f"Центральные работы (название — автор): {works_line}\n"
        f"Фрагменты: {snip_line}\n"
    )
    llm = _ollama_short(prompt, model, generate)
    if not llm:
        return _fallback(None, "no_llm")
    ok, hits, reason = _label_acceptable(
        llm, terms=terms, fragment_lexicon=fragment_lex, forbidden_labels=forbid
    )
    if not ok:
        retry_extra = (
            f"\nПовтор: не склеивай топ-термины через «и»; "
            f"обязательно оперся на стемы из: {', '.join(terms[:12])}. "
            "Если нельзя — NONE.\n"
        )
        if reason == "duplicate_theme_topic":
            retry_extra += f"Название не должно совпадать с: {', '.join(forbid[:8])}.\n"
        llm2 = _ollama_short(prompt + retry_extra, model, generate)
        if llm2:
            llm = llm2
            ok, hits, reason = _label_acceptable(
                llm, terms=terms, fragment_lexicon=fragment_lex, forbidden_labels=forbid
            )
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
    return _fallback(llm, reason)


def theme_affinity_stats(payload: dict[str, Any]) -> dict[str, Any]:
    """Inter-theme affinity = cut(A,B) / min(vol_A, vol_B).

    ``vol_T`` is the sum of weights of work-edges incident to works in theme T
    (undirected). ``cut(A,B)`` is the sum of weights of edges with ends in A and B.
    Matrix values and max_inter use the same formula (slice 2d).
    Service / unthemed themes are excluded.
    """
    themes = [
        t
        for t in (payload.get("themes") or [])
        if str(t.get("collection") or "") != "Прочее"
        and str(t.get("service") or "") != "unthemed"
        and int(t.get("size") or 0) > 0
    ]
    assign = payload.get("assignments") or {}
    ids = {str(t["id"]) for t in themes}
    topics = {str(t["id"]): t for t in (payload.get("topics") or [])}
    work_theme: dict[str, str] = {}
    for wk, row in (assign or {}).items():
        tid = (row or {}).get("theme_id")
        topic_id = (row or {}).get("topic_id")
        if topic_id and (topics.get(str(topic_id)) or {}).get("service") == "unthemed":
            continue
        if tid and str(tid) in ids:
            work_theme[str(wk)] = str(tid)

    deg: dict[str, float] = {}
    intra_cut: dict[str, float] = {tid: 0.0 for tid in ids}
    cross_cut: dict[tuple[str, str], float] = {}
    for edge in payload.get("work_edges") or []:
        wa = str(edge["source"])[5:]
        wb = str(edge["target"])[5:]
        w = float(edge.get("weight") or 0)
        if w <= 0:
            continue
        deg[wa] = deg.get(wa, 0.0) + w
        deg[wb] = deg.get(wb, 0.0) + w
        ta, tb = work_theme.get(wa), work_theme.get(wb)
        if not ta or not tb:
            continue
        if ta == tb:
            intra_cut[ta] = intra_cut.get(ta, 0.0) + w
        else:
            a, b = (ta, tb) if ta < tb else (tb, ta)
            cross_cut[(a, b)] = cross_cut.get((a, b), 0.0) + w

    vol: dict[str, float] = {tid: 0.0 for tid in ids}
    for wk, tid in work_theme.items():
        vol[tid] = vol.get(tid, 0.0) + float(deg.get(wk, 0.0))

    matrix: dict[tuple[str, str], float] = {}
    inter_vals: list[float] = []
    for (a, b), cut in cross_cut.items():
        denom = min(vol.get(a, 0.0), vol.get(b, 0.0))
        aff = (cut / denom) if denom > 1e-12 else 0.0
        matrix[(a, b)] = aff
        inter_vals.append(aff)

    intra_vals: list[float] = []
    for tid, cut in intra_cut.items():
        denom = vol.get(tid, 0.0)
        intra_vals.append((cut / denom) if denom > 1e-12 else 0.0)

    mean_intra = sum(intra_vals) / len(intra_vals) if intra_vals else 0.0
    mean_inter = sum(inter_vals) / len(inter_vals) if inter_vals else 0.0
    max_inter = max(inter_vals) if inter_vals else 0.0
    ratio = mean_intra / mean_inter if mean_inter > 1e-12 else 99.0
    return {
        "mean_intra": round(mean_intra, 4),
        "mean_inter": round(mean_inter, 4),
        "max_inter": round(max_inter, 4),
        "ratio": round(ratio, 4),
        "n_themes": len(ids),
        "matrix": {f"{a}|{b}": round(v, 4) for (a, b), v in matrix.items()},
        "formula": "cut/min(vol_a,vol_b)",
        "legacy_note": (
            "2c summary used mean edge weight / (|A|·|B|); "
            "2c matrix used raw_sum/sqrt(|A|·|B|). 2d unifies on conductance-style cut/min(vol)."
        ),
    }


def theme_link_stats(
    themes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if payload is not None:
        return theme_affinity_stats(payload)
    ids = [
        str(t["id"])
        for t in themes
        if str(t.get("collection") or "") != "Прочее" and str(t.get("service") or "") != "unthemed"
    ]
    inter_vals = []
    for edge in links:
        a, b = str(edge.get("source")), str(edge.get("target"))
        if a in ids and b in ids and a != b:
            inter_vals.append(float(edge.get("weight") or 0))
    max_inter = max(inter_vals) if inter_vals else 0.0
    mean_inter = sum(inter_vals) / len(inter_vals) if inter_vals else 0.0
    dens = [
        float(t.get("density") or 0)
        for t in themes
        if str(t.get("collection") or "") != "Прочее" and str(t.get("service") or "") != "unthemed"
    ]
    mean_intra = sum(dens) / len(dens) if dens else max(0.0, 1.0 - mean_inter)
    ratio = mean_intra / mean_inter if mean_inter > 1e-9 else 99.0
    return {
        "mean_intra": round(mean_intra, 4),
        "mean_inter": round(mean_inter, 4),
        "max_inter": round(max_inter, 4),
        "ratio": round(ratio, 4),
        "n_themes": len(ids),
        "formula": "cut/min(vol_a,vol_b)",
    }


def sweep_theme_resolution(
    index: dict[str, Any],
    base_params: dict[str, Any],
    *,
    resolutions: list[float] | None = None,
    work_terms: dict[str, Any] | None = None,
    case_stats: dict[str, dict[str, int]] | None = None,
    pos_stats: dict[str, dict[str, int]] | None = None,
    work_case_pos: dict[str, dict[str, dict[str, int]]] | None = None,
    tenant_id: str = "",
) -> list[dict[str, Any]]:
    """Grid search theme_resolution; metric = mean_intra / mean_inter."""
    from apps.api.services import graph_topics as gt

    grid = resolutions or [1.0, 1.3, 1.6, 2.0, 2.5, 3.0]
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
            work_case_pos=work_case_pos,
            force_mode="full",
            llm_generate=lambda _p, _m: "",
            tenant_id=tenant_id,
        )
        stats = theme_affinity_stats(payload)
        n_topics = int((payload.get("stats") or {}).get("topics") or 0)
        theme_purity = [
            float(t.get("author_purity") or 0)
            for t in (payload.get("themes") or [])
            if str(t.get("service") or "") != "unthemed"
            and str(t.get("collection") or "") != "Прочее"
            and t.get("author_purity") is not None
        ]
        mean_purity = (
            round(sum(theme_purity) / len(theme_purity), 4) if theme_purity else 0.0
        )
        ok = (
            stats["max_inter"] <= 0.2
            and 4 <= stats["n_themes"] <= 10
            and 15 <= n_topics <= 60
            and 0.35 <= mean_purity <= 0.65
        )
        ok_soft = (
            stats["max_inter"] <= 0.2
            and 4 <= stats["n_themes"] <= 10
            and 15 <= n_topics <= 60
        )
        rows.append(
            {
                "theme_resolution": res,
                **stats,
                "n_topics": n_topics,
                "mean_theme_purity": mean_purity,
                "ok": ok,
                "ok_soft": ok_soft,
            }
        )
    rows.sort(
        key=lambda r: (
            0 if r["ok"] else 1,
            0 if r.get("ok_soft") else 1,
            -float(r["ratio"]),
            abs(float(r["n_themes"]) - 7),
            abs(float(r.get("mean_theme_purity") or 0) - 0.5),
            float(r["max_inter"]),
        )
    )
    return rows
