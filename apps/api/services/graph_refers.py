"""Strict refers_to title matching (slice 1d / A1).

Full-title matches only; lowercase never counts; genre markers must sit
immediately before the title; quotes must equal the title exactly.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Iterable

from apps.api.services import query_intent as qi
from apps.api.services.graph_lexicon import frozenset_field, tuple_field
from apps.api.services.text_entities import (
    EDITION_STOP,
    REFERS_STOP_PHRASES,
    STOP,
    norm_ru,
    stem_ru,
)

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+", re.UNICODE)
_QUOTE_PAIRS = (("«", "»"), ('"', '"'), ("“", "”"), ("„", "“"), ("'", "'"))


def genre_markers() -> frozenset[str]:
    return frozenset(norm_ru(x) for x in tuple_field("genre_markers")) or frozenset(
        {
            "роман",
            "повесть",
            "рассказ",
            "поэма",
            "стихотворение",
            "пьеса",
            "драма",
            "комедия",
            "басня",
            "статья",
            "книга",
        }
    )


def genre_title_blocklist() -> frozenset[str]:
    return frozenset_field("genre_title_blocklist")


def commentary_markers() -> tuple[str, ...]:
    return tuple(norm_ru(x) for x in tuple_field("commentary_markers")) or (
        "примечания",
        "комментарии",
        "примечания редактора",
        "под редакцией",
        "псс",
        "сочинения и письма",
    )


def detect_zone(text: str, idx: int = 0) -> str:
    """Return commentary if idx falls after an editorial section marker."""
    body = norm_ru(text or "")
    if not body:
        return "body"
    cut = len(body)
    for m in commentary_markers():
        pos = body.find(m)
        if 0 <= pos < cut:
            cut = pos
    # footnote-ish lines near idx
    line_start = (text or "").rfind("\n", 0, idx) + 1
    line = (text or "")[line_start : idx + 40]
    if re.match(r"^\s*(\*|\[\d+\]|\d+\))", line):
        return "commentary"
    if idx >= cut and cut < len(body):
        return "commentary"
    return "body"


def title_tokens(title: str) -> list[str]:
    """All alnum tokens of a title (keep short words like «три», «на»)."""
    return [norm_ru(t) for t in _TOKEN_RE.findall(title or "") if t]


def is_multiword_title(title: str) -> bool:
    return len(title_tokens(title)) >= 2


def _tokens_with_spans(raw: str) -> list[tuple[str, int, int, str]]:
    """(norm, start, end, original_surface)."""
    out: list[tuple[str, int, int, str]] = []
    for m in _TOKEN_RE.finditer(raw or ""):
        surf = m.group(0)
        out.append((norm_ru(surf), m.start(), m.end(), surf))
    return out


_CASE_SUFFIXES = frozenset(
    {
        "",
        "а",
        "я",
        "у",
        "ю",
        "е",
        "ы",
        "и",
        "ой",
        "ей",
        "ом",
        "ем",
        "ам",
        "ям",
        "ах",
        "ях",
        "ою",
        "ею",
        "ым",
        "им",
        "ых",
        "их",
        "ую",
        "юю",
    }
)


def _token_match(text_tok: str, title_tok: str) -> bool:
    """Case/light-inflection match. Иван ≠ Иванов; Иванова ≈ Иванов."""
    if text_tok == title_tok:
        return True
    if len(title_tok) < 3 or len(text_tok) < 3:
        return False
    # text is title + case ending (Иванов → Иванова)
    if text_tok.startswith(title_tok) and text_tok[len(title_tok) :] in _CASE_SUFFIXES:
        return True
    # text is shorter case form only when title itself ends with that ending
    if title_tok.startswith(text_tok) and title_tok[len(text_tok) :] in _CASE_SUFFIXES:
        return True
    # shared long prefix + both differ by a case suffix only
    n = min(len(text_tok), len(title_tok))
    keep = max(4, n - 2)
    if text_tok[:keep] != title_tok[:keep]:
        return False
    if abs(len(text_tok) - len(title_tok)) > 3:
        return False
    # Reject surname/base mismatches (иван* vs иванов*)
    if title_tok.endswith(("ов", "ев", "ёв", "ин", "ын")) and not text_tok.startswith(title_tok[: max(4, len(title_tok) - 1)]):
        return False
    t_suf = title_tok[keep:]
    x_suf = text_tok[keep:]
    return t_suf in _CASE_SUFFIXES and x_suf in _CASE_SUFFIXES


def _is_sentence_start(raw: str, idx: int) -> bool:
    j = idx - 1
    while j >= 0 and raw[j] in " \t«\"“„":
        j -= 1
    if j < 0:
        return True
    if raw[j] in ".!?\n—–":
        return True
    # dialogue dash forms
    if raw[j] in "-–—" and (j == 0 or raw[j - 1] in "\n \t"):
        return True
    return False


def _exact_quoted_spans(raw: str) -> list[tuple[int, int, str]]:
    """(start, end, inner_text) for quote pairs."""
    spans: list[tuple[int, int, str]] = []
    for open_q, close_q in _QUOTE_PAIRS:
        start = 0
        text = raw or ""
        while True:
            a = text.find(open_q, start)
            if a < 0:
                break
            b = text.find(close_q, a + len(open_q))
            if b < 0:
                break
            inner = text[a + len(open_q) : b]
            spans.append((a + len(open_q), b, inner))
            start = b + len(close_q)
    return spans


def _quote_equals_title(inner: str, title_tokens_n: list[str]) -> bool:
    inner_n = norm_ru(inner).strip(" .,:;«»\"'")
    toks = title_tokens(inner_n)
    if not toks or len(toks) != len(title_tokens_n):
        return False
    return all(_token_match(a, b) for a, b in zip(toks, title_tokens_n))


def commentary_cut(raw: str) -> int:
    """Index in norm_ru(raw) where commentary section begins (or len)."""
    body = norm_ru(raw or "")
    cut = len(body)
    for m in commentary_markers():
        pos = body.find(m)
        if 0 <= pos < cut:
            cut = pos
    return cut


def detect_zone_fast(raw: str, idx: int, cut: int | None = None) -> str:
    if cut is None:
        return detect_zone(raw, idx)
    line_start = (raw or "").rfind("\n", 0, idx) + 1
    line = (raw or "")[line_start : idx + 40]
    if re.match(r"^\s*(\*|\[\d+\]|\d+\))", line):
        return "commentary"
    body = norm_ru(raw or "")
    if idx >= cut and cut < len(body):
        return "commentary"
    return "body"


def find_title_hits(
    raw: str,
    title_surface: str,
    *,
    tokens: list[tuple[str, int, int, str]] | None = None,
    quoted_spans: list[tuple[int, int, str]] | None = None,
    zone_cut: int | None = None,
    first_index: dict[str, list[int]] | None = None,
) -> list[dict[str, Any]]:
    """Find full-title hits with match_form classification inputs.

    Optional precomputed ``tokens`` / ``quoted_spans`` / ``zone_cut`` / ``first_index``
    avoid rescanning the same chunk for every title candidate.
    """
    toks = title_tokens(title_surface)
    if not toks:
        return []
    multi = len(toks) >= 2
    hits: list[dict[str, Any]] = []
    if tokens is None:
        tokens = _tokens_with_spans(raw)
    if quoted_spans is None:
        quoted_spans = _exact_quoted_spans(raw)
    if zone_cut is None:
        zone_cut = commentary_cut(raw)

    # Exact quote matches first
    for a, b, inner in quoted_spans:
        if not _quote_equals_title(inner, toks):
            continue
        inner_stripped = inner.lstrip()
        if not inner_stripped:
            continue
        if not inner_stripped[0].isupper() and not inner_stripped.isupper():
            if not any(ch.isupper() for ch in inner_stripped):
                continue
        hits.append(
            {
                "start": a,
                "end": b,
                "match_form": "quoted",
                "multiword": multi,
                "zone": detect_zone_fast(raw, a, zone_cut),
            }
        )

    # Full token-sequence matches — jump by first-token index when available
    n = len(toks)
    if n == 0 or len(tokens) < n:
        return hits
    start_positions: list[int]
    if first_index is not None:
        keys = {toks[0], stem_ru(toks[0])}
        start_positions = sorted({i for k in keys for i in (first_index.get(k) or [])})
    else:
        start_positions = list(range(0, len(tokens) - n + 1))
    for i in start_positions:
        if i + n > len(tokens):
            continue
        window = tokens[i : i + n]
        if not all(_token_match(window[j][0], toks[j]) for j in range(n)):
            continue
        start, end = window[0][1], window[-1][2]
        if any(h["start"] <= start < h["end"] or start <= h["start"] < end for h in hits):
            continue
        first_surf = window[0][3]
        if not first_surf or not first_surf[0].isupper():
            continue
        hits.append(
            {
                "start": start,
                "end": end,
                "match_form": None,
                "multiword": multi,
                "zone": detect_zone_fast(raw, start, zone_cut),
                "capitalized": not _is_sentence_start(raw, start),
                "token_index": i,
            }
        )
    return hits


def _tokens_before(tokens: list[tuple[str, int, int, str]], hit_start: int, k: int = 2) -> list[str]:
    before = [t for t in tokens if t[2] <= hit_start]
    return [t[0] for t in before[-k:]]


def _near_author_tokens(
    tokens: list[tuple[str, int, int, str]],
    hit_start: int,
    hit_end: int,
    author_key: str | None,
    radius: int = 8,
) -> bool:
    if not author_key:
        return False
    forms = {norm_ru(s) for s, k in qi.AUTHOR_ALIASES.items() if k == author_key}
    forms.add(norm_ru(author_key))
    # find hit token range
    hit_idxs = [i for i, t in enumerate(tokens) if not (t[2] <= hit_start or t[1] >= hit_end)]
    if not hit_idxs:
        return False
    lo, hi = min(hit_idxs), max(hit_idxs)
    for i, (n, _a, _b, _s) in enumerate(tokens):
        if abs(i - lo) <= radius or abs(i - hi) <= radius:
            if n in forms or any(n.startswith(stem_ru(f)) and abs(len(n) - len(f)) <= 2 for f in forms if len(f) >= 4):
                return True
    return False


def question_title_starters() -> frozenset[str]:
    return frozenset_field("question_title_starters") or frozenset(
        {"кто", "что", "где", "куда", "когда", "почему", "зачем", "как", "чей", "чья", "чье", "чьё"}
    )


def foreign_marker_near(raw: str, start: int, end: int, window: int = 40) -> bool:
    snip = (raw or "")[max(0, start - window) : end + window]
    n = norm_ru(snip)
    if re.search(r"\((франц|нем|англ|итал|лат|польск)", n):
        return True
    if re.search(r"\b(de l'|dell'|von |van |the )\b", snip, flags=re.I):
        return True
    return False


def classify_hit(
    raw: str,
    hit: dict[str, Any],
    *,
    author_key: str | None,
    same_author: bool,
    frequent_title: bool,
    person_name_title: bool = False,
    question_title: bool = False,
) -> str | None:
    """Apply A1/E6 strong-form rules; return match_form or None."""
    if hit.get("match_form") == "quoted":
        form = "quoted"
    else:
        form = None
        tokens = _tokens_with_spans(raw)
        before = _tokens_before(tokens, hit["start"], 2)
        markers = genre_markers()
        genre_ok = any(b in markers for b in before)
        cap = bool(hit.get("capitalized"))
        near = _near_author_tokens(tokens, hit["start"], hit["end"], author_key)
        multi = bool(hit.get("multiword"))

        if genre_ok and (cap or True):
            form = "genre_marker"
        elif near and (cap or hit.get("match_form") == "quoted"):
            form = "near_author"
        elif multi and cap and not frequent_title and not same_author:
            form = "capitalized"
        else:
            return None

        # One-word: only quoted or genre_marker
        if not multi and form not in ("quoted", "genre_marker"):
            return None
        # E6: person-name one-word titles and question-starters → quoted only
        if (person_name_title or question_title) and form != "quoted":
            return None
        # Same-author works: only quoted/genre
        if same_author and form not in ("quoted", "genre_marker"):
            return None
        # Frequent titles: only quoted/genre even if multiword
        if frequent_title and form not in ("quoted", "genre_marker"):
            return None

    if form == "quoted":
        return "quoted"
    return form


def build_title_phrases(docs: list[Any]) -> list[tuple[str, str, str, bool]]:
    """(norm_phrase, doc_id, surface, multiword) from full titles only."""
    items: list[tuple[str, str, str, bool]] = []
    seen: set[tuple[str, str]] = set()
    block = genre_title_blocklist() | genre_markers()
    for d in docs:
        title = (getattr(d, "title", None) or "").strip()
        toks = title_tokens(title)
        if not toks:
            continue
        multi = len(toks) >= 2
        n = " ".join(toks)
        if len(n) < 3:
            continue
        if n in REFERS_STOP_PHRASES or n in STOP or n in EDITION_STOP:
            continue
        if n in qi.AUTHOR_ALIASES:
            continue
        if n in block:
            continue
        # Never index single leftover function-ish tokens from stripped titles
        if not multi and (n in STOP or len(n) < 4):
            continue
        key = (n, d.document_id)
        if key in seen:
            continue
        seen.add(key)
        items.append((n, d.document_id, title, multi))
    items.sort(key=lambda x: (-len(x[0]), x[0], x[1]))
    return items


def lower_token_df(chunk_iter: Iterable[tuple[str, str, str]]) -> Counter[str]:
    """Document-frequency of lowercase token forms across chunks (approx by doc)."""
    per_doc: dict[str, set[str]] = defaultdict(set)
    for doc_id, _cid, text in chunk_iter:
        for m in _TOKEN_RE.finditer(text or ""):
            surf = m.group(0)
            if surf and surf[0].islower():
                per_doc[doc_id].add(norm_ru(surf))
    df: Counter[str] = Counter()
    for toks in per_doc.values():
        for t in toks:
            df[t] += 1
    return df


def frequent_title_norms(
    phrases: list[tuple[str, str, str, bool]],
    lower_df: Counter[str],
    n_docs: int,
    *,
    top_frac: float = 0.20,
) -> set[str]:
    """Titles whose lower form is among frequent corpus tokens."""
    if n_docs <= 0 or not lower_df:
        return set()
    ranked = lower_df.most_common()
    cutoff_i = max(1, int(len(ranked) * top_frac))
    frequent = {t for t, _ in ranked[:cutoff_i]}
    # also absolute df threshold
    abs_df = max(3, int(0.05 * n_docs))
    out: set[str] = set()
    for phrase_n, _did, _surf, multi in phrases:
        # check last token or whole phrase
        parts = phrase_n.split()
        if any(p in frequent or lower_df.get(p, 0) >= abs_df for p in parts):
            out.add(phrase_n)
        if not multi and phrase_n in frequent:
            out.add(phrase_n)
    return out


def context_snip(text: str, idx: int, end: int, width: int = 120) -> str:
    a = max(0, idx - width // 3)
    b = min(len(text), end + width // 2)
    return re.sub(r"\s+", " ", text[a:b]).strip()
