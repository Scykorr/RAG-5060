"""Lexical document connectivity index (no Qdrant, no embeddings, no UI).

Slice 1b: work_key / part_of, strict refers_to, cleaned entities, fixed RU lex_similar.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from apps.api.models import ChatMessage, ChatSession, Chunk, Document, DocumentVersion
from apps.api.services import query_intent as qi
from apps.api.services.quality_state import corpus_fingerprint
from apps.api.services.storage import data_root
from apps.api.services.graph_text_clean import clean_chunk_for_graph
from apps.api.services.text_entities import (
    EDITION_STOP,
    ENTITY_DENY,
    REFERS_STOP_PHRASES,
    RU_FUNCTION_WORDS,
    STOP,
    clean_entity_surface,
    extract_entity_candidates,
    norm_ru,
    stem_ru,
    tokenize_lex,
)

log = structlog.get_logger()

INDEX_VERSION = 3

# Source-site tokens observed in classic_* filenames (probe 2026-11-10).
SOURCE_TOKENS = frozenset({"ch", "eb"})
LANGUAGE_MARKERS = frozenset({"russian", "english", "deutsch", "french"})

DEFAULT_CHUNK_BATCH = 200
DEFAULT_ENTITY_TOP_N = 20
DEFAULT_SHARED_MIN = 2
DEFAULT_SHARED_TOP_K = 8
DEFAULT_LEX_TOP_M = 50
DEFAULT_LEX_TOP_K = 5
DEFAULT_LEX_MIN_SCORE = 0.10
DEFAULT_REFERS_TOP_K = 10
DEFAULT_REFERS_HUB_FRAC = 0.10
DEFAULT_ENTITY_MAX_WORK_FRAC = 0.15
DEFAULT_REFERS_COMMON_WORK_DF = 3
DEFAULT_WORK_NEIGHBOR_TOP_K = 10

REFERS_GENRE_MARKERS = (
    "роман",
    "повесть",
    "рассказ",
    "поэма",
    "стихотворение",
    "пьеса",
    "читал",
    "читала",
    "автор",
)

_AUTHOR_FIRST_NAMES = (
    "александр",
    "александра",
    "федор",
    "фёдор",
    "лев",
    "антон",
    "николай",
    "михаил",
    "иван",
    "максим",
    "валерий",
    "алексей",
    "сергей",
)


@dataclass
class GraphIndexParams:
    chunk_batch: int = DEFAULT_CHUNK_BATCH
    entity_top_n: int = DEFAULT_ENTITY_TOP_N
    shared_min_entities: int = DEFAULT_SHARED_MIN
    shared_top_k: int = DEFAULT_SHARED_TOP_K
    lex_top_m: int = DEFAULT_LEX_TOP_M
    lex_top_k: int = DEFAULT_LEX_TOP_K
    lex_min_score: float = DEFAULT_LEX_MIN_SCORE
    refers_top_k: int = DEFAULT_REFERS_TOP_K
    refers_hub_frac: float = DEFAULT_REFERS_HUB_FRAC
    entity_max_work_frac: float = DEFAULT_ENTITY_MAX_WORK_FRAC
    refers_common_work_df: int = DEFAULT_REFERS_COMMON_WORK_DF
    work_neighbor_top_k: int = DEFAULT_WORK_NEIGHBOR_TOP_K
    include_eval_cocited: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_batch": self.chunk_batch,
            "entity_top_n": self.entity_top_n,
            "shared_min_entities": self.shared_min_entities,
            "shared_top_k": self.shared_top_k,
            "lex_top_m": self.lex_top_m,
            "lex_top_k": self.lex_top_k,
            "lex_min_score": self.lex_min_score,
            "refers_top_k": self.refers_top_k,
            "refers_hub_frac": self.refers_hub_frac,
            "entity_max_work_frac": self.entity_max_work_frac,
            "refers_common_work_df": self.refers_common_work_df,
            "work_neighbor_top_k": self.work_neighbor_top_k,
            "include_eval_cocited": self.include_eval_cocited,
            "index_version": INDEX_VERSION,
        }


@dataclass
class DocMeta:
    document_id: str
    filename: str
    theme: str
    pack: str
    source: str | None
    author_key: str | None
    author_ru: str | None
    title: str
    fragment_no: int | None = None
    work_key: str = ""
    chunk_count: int = 0
    created_at: str | None = None


@dataclass
class Edge:
    source: str
    target: str
    type: str
    weight: float
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "weight": self.weight,
            "provenance": self.provenance,
        }


_AUTHOR_RU_BY_KEY: dict[str, str] = {}
for _ru, _key in qi.AUTHOR_ALIASES.items():
    _AUTHOR_RU_BY_KEY.setdefault(_key, _ru)


def theme_from_filename(name: str) -> str:
    base = Path(name or "").name
    m = re.match(r"^([A-Za-z][A-Za-z0-9]*)_", base)
    if not m:
        return "Прочее"
    prefix = m.group(1).lower()
    if prefix in ("tmp", "file", "doc", "new", "copy"):
        return "Прочее"
    return f"{prefix}_"


def _strip_hash_and_fragment(base: str) -> tuple[str, int | None]:
    frag: int | None = None
    m = re.search(r"__фрагмент_(\d+)", base, flags=re.I)
    if m:
        frag = int(m.group(1))
        base = re.sub(r"__фрагмент_\d+", "", base, flags=re.I)
    base = re.sub(r"_[0-9a-f]{8,}$", "", base, flags=re.I)
    return base, frag


def parse_filename_parts(name: str) -> dict[str, Any]:
    """Parse pack/src/author/title/fragment from corpus filenames."""
    raw = Path(name or "").name
    stem = raw.rsplit(".", 1)[0]
    stem, frag = _strip_hash_and_fragment(stem)
    parts = [p for p in stem.split("_") if p != ""]
    pack = parts[0].lower() if parts else ""
    theme = theme_from_filename(raw)
    source: str | None = None
    author_key: str | None = None
    title_parts: list[str] = []

    if len(parts) >= 3 and pack.startswith("classic") and re.fullmatch(r"\d{3,}", parts[1] or ""):
        i = 2
        if parts[i].lower() in SOURCE_TOKENS:
            source = parts[i].lower()
            i += 1
        # author?
        if i < len(parts):
            tok = parts[i]
            if tok.lower() in LANGUAGE_MARKERS:
                # no author — language marker, skip from title
                i += 1
            elif tok in _AUTHOR_RU_BY_KEY or tok.capitalize() in _AUTHOR_RU_BY_KEY:
                author_key = tok if tok in _AUTHOR_RU_BY_KEY else tok.capitalize()
                i += 1
            elif re.fullmatch(r"[A-Z][A-Za-z\-]{3,}", tok):
                author_key = tok
                i += 1
        title_parts = parts[i:]
    elif len(parts) >= 2:
        title_parts = parts[1:]
    else:
        title_parts = parts

    # Drop leftover language/source/author tokens from title
    cleaned: list[str] = []
    for t in title_parts:
        tl = t.lower()
        if tl in SOURCE_TOKENS or tl in LANGUAGE_MARKERS:
            continue
        if author_key and (t == author_key or t.lower() == author_key.lower()):
            continue
        if t in _AUTHOR_RU_BY_KEY or t.capitalize() in _AUTHOR_RU_BY_KEY:
            # stray author token in title
            if not author_key:
                author_key = t if t in _AUTHOR_RU_BY_KEY else t.capitalize()
            continue
        cleaned.append(t)
    title = " ".join(cleaned).replace("  ", " ").strip(" _") or stem.replace("_", " ")
    author_ru = None
    if author_key:
        author_ru = _AUTHOR_RU_BY_KEY.get(author_key) or _AUTHOR_RU_BY_KEY.get(author_key.capitalize())
    return {
        "pack": pack or "other",
        "theme": theme,
        "source": source,
        "author_key": author_key,
        "author_ru": author_ru,
        "title": title,
        "fragment_no": frag,
    }


def work_key_for(*, pack: str, author_key: str | None, title: str) -> str:
    a = (author_key or "").strip() or "_"
    t = re.sub(r"\s+", " ", norm_ru(title)).strip()
    t = re.sub(r"[^0-9a-zа-я ]+", "", t)
    t = re.sub(r"\s+", "_", t)[:80] or "untitled"
    return f"{pack}|{a}|{t}"


def parse_doc_meta(
    *,
    document_id: str,
    filename: str,
    chunk_count: int = 0,
    created_at: str | None = None,
) -> DocMeta:
    parsed = parse_filename_parts(filename)
    wk = work_key_for(pack=parsed["pack"], author_key=parsed["author_key"], title=parsed["title"])
    return DocMeta(
        document_id=str(document_id),
        filename=filename,
        theme=parsed["theme"],
        pack=parsed["pack"],
        source=parsed["source"],
        author_key=parsed["author_key"],
        author_ru=parsed["author_ru"],
        title=parsed["title"],
        fragment_no=parsed["fragment_no"],
        work_key=wk,
        chunk_count=chunk_count,
        created_at=created_at,
    )


def resolve_work_keys(docs: list[DocMeta]) -> None:
    """Merge authorless unique titles into authored work_key when unique in corpus."""
    by_title: dict[str, list[DocMeta]] = defaultdict(list)
    for d in docs:
        by_title[norm_ru(d.title)].append(d)
    authored_title: dict[str, str] = {}
    for title_n, group in by_title.items():
        authors = {d.author_key for d in group if d.author_key}
        if len(authors) == 1:
            authored_title[title_n] = next(iter(authors))  # type: ignore[arg-type]
    for d in docs:
        if d.author_key:
            continue
        ak = authored_title.get(norm_ru(d.title))
        if not ak:
            continue
        # only if that title maps to exactly one author across corpus
        d.author_key = ak
        d.author_ru = _AUTHOR_RU_BY_KEY.get(ak)
        d.work_key = work_key_for(pack=d.pack, author_key=d.author_key, title=d.title)


def layer_a_edges(docs: list[DocMeta]) -> list[Edge]:
    edges: list[Edge] = []
    work_docs: dict[str, list[DocMeta]] = defaultdict(list)
    for d in docs:
        work_docs[d.work_key].append(d)
        edges.append(
            Edge(
                source=f"theme:{d.theme}",
                target=d.document_id,
                type="in_theme",
                weight=1.0,
                provenance={"theme": d.theme, "filename": d.filename},
            )
        )
        if d.author_key:
            edges.append(
                Edge(
                    source=f"author:{d.author_key}",
                    target=d.document_id,
                    type="wrote",
                    weight=1.0,
                    provenance={
                        "author_key": d.author_key,
                        "author_ru": d.author_ru,
                        "filename": d.filename,
                    },
                )
            )
        edges.append(
            Edge(
                source=d.document_id,
                target=f"work:{d.work_key}",
                type="part_of",
                weight=1.0,
                provenance={
                    "work_key": d.work_key,
                    "fragment_no": d.fragment_no,
                    "title": d.title,
                },
            )
        )
    # work meta edges: self-describing via provenance on a synthetic note stored as edge weight=fragment_count
    for wk, group in work_docs.items():
        edges.append(
            Edge(
                source=f"work:{wk}",
                target=f"work:{wk}",
                type="work_meta",
                weight=float(len(group)),
                provenance={
                    "work_key": wk,
                    "fragment_count": len(group),
                    "title": group[0].title,
                    "author_key": group[0].author_key,
                    "document_ids": [g.document_id for g in group[:20]],
                },
            )
        )
    return edges


def same_work(a: DocMeta | None, b: DocMeta | None) -> bool:
    if not a or not b:
        return False
    return bool(a.work_key) and a.work_key == b.work_key


# ---------------------------------------------------------------------------
# Layer B — mentions_author + refers_to (works only, strong forms)
# ---------------------------------------------------------------------------

def _significant_words(title: str) -> list[str]:
    words = []
    for w in re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", title or "", flags=re.UNICODE):
        n = norm_ru(w)
        if n in STOP or n in ENTITY_DENY or n in REFERS_STOP_PHRASES or n in RU_FUNCTION_WORDS:
            continue
        words.append(w)
    return words


def _author_forms_by_key() -> dict[str, list[str]]:
    """author_key → RU surface forms (from AUTHOR_ALIASES), longest first."""
    out: dict[str, list[str]] = defaultdict(list)
    for surface, key in qi.AUTHOR_ALIASES.items():
        out[key].append(surface)
    for key, forms in out.items():
        out[key] = sorted(set(forms), key=len, reverse=True)
    return dict(out)


_AUTHOR_FORMS_CACHE: dict[str, list[str]] | None = None


def author_forms_by_key() -> dict[str, list[str]]:
    global _AUTHOR_FORMS_CACHE
    if _AUTHOR_FORMS_CACHE is None:
        _AUTHOR_FORMS_CACHE = _author_forms_by_key()
    return _AUTHOR_FORMS_CACHE


def _is_sentence_start(text: str, idx: int) -> bool:
    """True if idx is at the start of a sentence / dialogue turn."""
    j = idx - 1
    while j >= 0 and text[j] in " \t«\"“„":
        j -= 1
    if j < 0:
        return True
    if text[j] in ".!?\n—–":
        return True
    return False


def _window(text: str, idx: int, length: int, radius: int = 48) -> str:
    a = max(0, idx - radius)
    b = min(len(text), idx + length + radius)
    return text[a:b]


def _has_genre_marker_near(raw: str, idx: int, length: int) -> bool:
    win = norm_ru(_window(raw, idx, length, 40))
    return any(m in win for m in REFERS_GENRE_MARKERS)


def _quoted_span(raw: str, idx: int, length: int) -> bool:
    left = raw[max(0, idx - 1) : idx]
    right = raw[idx + length : idx + length + 1]
    quotes = set("«»\"“”„'")
    if left and left in quotes:
        return True
    if right and right in quotes:
        return True
    # opening quote a few chars before
    pre = raw[max(0, idx - 2) : idx]
    return any(c in quotes for c in pre)


def _capitalized_mid_sentence(raw: str, idx: int) -> bool:
    if idx >= len(raw) or not raw[idx].isupper():
        return False
    return not _is_sentence_start(raw, idx)


def classify_refers_match(
    raw: str,
    idx: int,
    match_len: int,
    *,
    multiword: bool,
    requires_strong: bool,
    near_author: bool,
) -> str | None:
    """Return match_form or None if the hit must be discarded."""
    if _quoted_span(raw, idx, match_len):
        return "quoted"
    if near_author:
        return "near_author"
    if _has_genre_marker_near(raw, idx, match_len):
        return "genre_marker"
    if _capitalized_mid_sentence(raw, idx):
        return "capitalized"
    if requires_strong:
        return None
    if multiword:
        return "multiword_plain"
    return None


def find_author_mentions_in_text(raw: str) -> list[tuple[str, str, int, int]]:
    """Return (author_key, surface, start, end) for capitalized / initialed mentions.

    Lowercase adjective-like forms (горько, горький as mid-sentence lower) are skipped
    unless initials or a known first name sits next to the surname.
    """
    if not raw:
        return []
    body = norm_ru(raw)
    hits: list[tuple[str, str, int, int]] = []
    forms = author_forms_by_key()
    # Initials + surname: «М. Горький», «Л. Н. Толстой», «Ф.М.Достоевский»
    init_pat = re.compile(
        r"(?<![0-9A-Za-zА-Яа-яЁё])([A-ZА-ЯЁ]\.\s*){1,3}"
        r"([A-ZА-ЯЁ][a-zа-яёA-ZА-ЯЁ\-]{2,40})"
        r"(?![0-9A-Za-zА-Яа-яЁё])",
        re.UNICODE,
    )
    for m in init_pat.finditer(raw):
        surname = m.group(2)
        sn = norm_ru(surname)
        for key, surfaces in forms.items():
            if sn in surfaces or any(sn.startswith(stem_ru(s)) and abs(len(sn) - len(s)) <= 2 for s in surfaces):
                hits.append((key, m.group(0), m.start(), m.end()))
                break

    for key, surfaces in forms.items():
        for surface in surfaces:
            start = 0
            while True:
                idx = body.find(surface, start)
                if idx < 0:
                    break
                end = idx + len(surface)
                left_ok = idx == 0 or not body[idx - 1].isalnum()
                right_ok = end >= len(body) or not body[end].isalnum()
                start = idx + 1
                if not (left_ok and right_ok):
                    continue
                # Map to raw index (norm_ru only lowercases / ё→е; lengths match for Cyrillic)
                raw_ch = raw[idx] if idx < len(raw) else ""
                win = norm_ru(_window(raw, idx, len(surface), 24))
                has_first = any(fn in win for fn in _AUTHOR_FIRST_NAMES)
                has_initials = bool(
                    re.search(
                        r"[A-ZА-ЯЁ]\.\s*" + re.escape(surface[:1]),
                        raw[max(0, idx - 8) : end + 1],
                    )
                )
                if raw_ch.isupper() or has_first or has_initials:
                    hits.append((key, raw[idx:end], idx, end))
    # Dedup overlapping (keep longer surface)
    hits.sort(key=lambda h: (h[2], -(h[3] - h[2])))
    out: list[tuple[str, str, int, int]] = []
    last_end = -1
    for h in hits:
        if h[2] < last_end:
            continue
        out.append(h)
        last_end = h[3]
    return out


def layer_mentions_author(
    *,
    docs: list[DocMeta],
    chunk_iter: Iterable[tuple[str, str, str]],
) -> list[Edge]:
    """document → author:<key> when clean text mentions another author."""
    by_id = {d.document_id: d for d in docs}
    # doc -> author_key -> {chunks, snips}
    hits: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"chunks": [], "snips": []})
    )
    for doc_id, chunk_id, text in chunk_iter:
        raw = text or ""
        src = by_id.get(doc_id)
        for key, surface, idx, end in find_author_mentions_in_text(raw):
            if src and src.author_key and key == src.author_key:
                continue  # self-mention
            bucket = hits[doc_id][key]
            if chunk_id not in bucket["chunks"]:
                bucket["chunks"].append(chunk_id)
            if len(bucket["snips"]) < 3:
                bucket["snips"].append(_context_snip(raw, idx, end - idx))

    edges: list[Edge] = []
    for doc_id, by_auth in hits.items():
        for key, info in by_auth.items():
            edges.append(
                Edge(
                    source=doc_id,
                    target=f"author:{key}",
                    type="mentions_author",
                    weight=float(len(info["chunks"])),
                    provenance={
                        "author_key": key,
                        "chunk_ids": info["chunks"][:5],
                        "context": (info["snips"][0] if info["snips"] else "")[:160],
                        "contexts": info["snips"][:3],
                    },
                )
            )
    return edges


def build_refers_phrase_index(
    docs: list[DocMeta],
) -> list[tuple[str, str, str, bool]]:
    """(norm_phrase, document_id, surface, is_multiword) — titles only, no author names."""
    items: list[tuple[str, str, str, bool]] = []
    seen: set[tuple[str, str]] = set()

    def add(surface: str, doc_id: str, *, multi: bool) -> None:
        s = (surface or "").strip(" .,:;«»\"'")
        if not s:
            return
        n = norm_ru(s)
        if len(n) < 3:
            return
        if n in REFERS_STOP_PHRASES or n in STOP or n in EDITION_STOP:
            return
        # Never index author surnames as work phrases
        if n in qi.AUTHOR_ALIASES:
            return
        key = (n, doc_id)
        if key in seen:
            return
        seen.add(key)
        items.append((n, doc_id, s, multi))

    for d in docs:
        words = _significant_words(d.title)
        if len(words) >= 2:
            add(" ".join(words), d.document_id, multi=True)
            add(d.title, d.document_id, multi=True)
        elif len(words) == 1:
            add(words[0], d.document_id, multi=False)
            add(d.title, d.document_id, multi=False)

    items.sort(key=lambda x: (-len(x[0]), x[0], x[1]))
    return items


def _word_boundary_find(
    body: str, phrase: str, *, allow_multi_stem: bool = True
) -> list[tuple[int, int, bool]]:
    """Find (start, length, exact) with alnum boundaries; optional multiword stem."""
    hits: list[tuple[int, int, bool]] = []
    if not phrase:
        return hits
    start = 0
    while True:
        idx = body.find(phrase, start)
        if idx < 0:
            break
        end = idx + len(phrase)
        left_ok = idx == 0 or not body[idx - 1].isalnum()
        right_ok = end >= len(body) or not body[end].isalnum()
        if left_ok and right_ok:
            hits.append((idx, len(phrase), True))
        start = idx + 1
    if hits or not allow_multi_stem:
        return hits
    parts = phrase.split()
    if len(parts) < 2:
        return hits
    mid = parts[1:-1]
    first_stem = stem_ru(parts[0])
    last_stem = stem_ru(parts[-1])
    if len(first_stem) < 4 or len(last_stem) < 4:
        return hits
    mid_re = r"\s+".join(re.escape(m) for m in mid) if mid else ""
    if mid_re:
        mid_re = r"\s+" + mid_re + r"\s+"
    else:
        mid_re = r"\s+"
    pat = re.compile(
        rf"(?<![0-9a-zа-я]){re.escape(first_stem)}[a-zа-я]{{0,4}}{mid_re}"
        rf"{re.escape(last_stem)}[a-zа-я]{{0,4}}(?![0-9a-zа-я])",
        re.UNICODE,
    )
    for m in pat.finditer(body):
        hits.append((m.start(), m.end() - m.start(), False))
    return hits


def _context_snip(text: str, idx: int, phrase_len: int, width: int = 120) -> str:
    a = max(0, idx - width // 3)
    b = min(len(text), idx + phrase_len + width // 2)
    return re.sub(r"\s+", " ", text[a:b]).strip()


def _phrase_work_df(
    phrases: list[tuple[str, str, str, bool]],
    docs: list[DocMeta],
    chunk_iter: Iterable[tuple[str, str, str]],
) -> dict[str, int]:
    """How many distinct works contain each norm phrase (plain occurrence)."""
    by_id = {d.document_id: d for d in docs}
    uniq = sorted({p[0] for p in phrases}, key=len, reverse=True)
    # first-token bucket to avoid scanning every phrase on every chunk
    by_first: dict[str, list[str]] = defaultdict(list)
    for p in uniq:
        first = p.split()[0] if p else p
        by_first[first].append(p)
    works_with: dict[str, set[str]] = defaultdict(set)
    for doc_id, _cid, text in chunk_iter:
        meta = by_id.get(doc_id)
        if not meta:
            continue
        body = norm_ru(text or "")
        if not body:
            continue
        seen_here: set[str] = set()
        # candidate first tokens present in body
        for first, plist in by_first.items():
            if first not in body:
                continue
            for phrase_n in plist:
                if phrase_n in seen_here:
                    continue
                if _word_boundary_find(body, phrase_n, allow_multi_stem=False):
                    seen_here.add(phrase_n)
                    works_with[phrase_n].add(meta.work_key)
    return {p: len(ws) for p, ws in works_with.items()}


def _authors_in_chunk(raw: str) -> set[str]:
    return {k for k, _, _, _ in find_author_mentions_in_text(raw)}


def layer_b_refers_to(
    *,
    docs: list[DocMeta],
    chunk_iter: Iterable[tuple[str, str, str]],
    params: GraphIndexParams | None = None,
    phrase_work_df: dict[str, int] | None = None,
) -> list[Edge]:
    """refers_to: only work-title phrases; common/one-word need a strong form."""
    params = params or GraphIndexParams()
    by_id = {d.document_id: d for d in docs}
    phrases = build_refers_phrase_index(docs)
    if not phrases:
        return []

    chunk_list = list(chunk_iter)
    df = phrase_work_df if phrase_work_df is not None else _phrase_work_df(phrases, docs, chunk_list)

    # first-token index: first_norm_token -> list of phrase tuples
    by_first: dict[str, list[tuple[str, str, str, bool]]] = defaultdict(list)
    for item in phrases:
        first = item[0].split()[0]
        by_first[first].append(item)

    hits: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "chunks": [],
                "phrase": "",
                "multi": False,
                "snips": [],
                "match_form": "",
            }
        )
    )

    for doc_id, chunk_id, text in chunk_list:
        raw = text or ""
        body = norm_ru(raw)
        if not body:
            continue
        src_meta = by_id.get(doc_id)
        chunk_authors = _authors_in_chunk(raw)
        candidates: list[tuple[str, str, str, bool]] = []
        for first, plist in by_first.items():
            if first in body:
                candidates.extend(plist)
        # longest first
        candidates.sort(key=lambda x: -len(x[0]))
        matched_spans: list[tuple[int, int]] = []
        for phrase_n, target_id, surface, multi in candidates:
            if target_id == doc_id:
                continue
            tgt_meta = by_id.get(target_id)
            if same_work(src_meta, tgt_meta):
                continue
            found = _word_boundary_find(body, phrase_n, allow_multi_stem=multi)
            if not found:
                continue
            work_df = df.get(phrase_n, 0)
            common_phrase = work_df > params.refers_common_work_df
            near_author = bool(tgt_meta and tgt_meta.author_key and tgt_meta.author_key in chunk_authors)
            accepted = False
            best_form = ""
            best_idx = found[0][0]
            best_len = found[0][1]
            for idx, mlen, exact in found:
                # skip if overlaps a longer already-accepted match
                if any(not (idx + mlen <= a or idx >= b) for a, b in matched_spans):
                    continue
                # One-word titles, common collocations, and inflected (stem) hits
                # need a strong form; plain multiword exact hits may pass.
                requires_strong = (not multi) or common_phrase or (not exact)
                form = classify_refers_match(
                    raw,
                    idx,
                    mlen,
                    multiword=multi,
                    requires_strong=requires_strong,
                    near_author=near_author,
                )
                if form:
                    accepted = True
                    best_form = form
                    best_idx = idx
                    best_len = mlen
                    break
            if not accepted:
                continue
            matched_spans.append((best_idx, best_idx + best_len))
            bucket = hits[doc_id][target_id]
            if chunk_id not in bucket["chunks"]:
                bucket["chunks"].append(chunk_id)
            if not bucket["phrase"] or (multi and not bucket["multi"]):
                bucket["phrase"] = surface
                bucket["multi"] = multi
                bucket["match_form"] = best_form
            elif not bucket["match_form"]:
                bucket["match_form"] = best_form
            if len(bucket["snips"]) < 2:
                bucket["snips"].append(_context_snip(raw, best_idx, best_len))

    raw_edges: list[Edge] = []
    for src, by_tgt in hits.items():
        for tgt, info in by_tgt.items():
            w = float(len(info["chunks"]))
            multi = bool(info["multi"])
            if w < 1:
                continue
            raw_edges.append(
                Edge(
                    source=src,
                    target=tgt,
                    type="refers_to",
                    weight=w,
                    provenance={
                        "chunk_ids": info["chunks"][:3],
                        "phrase": info["phrase"],
                        "context": (info["snips"][0] if info["snips"] else "")[:160],
                        "multiword": multi,
                        "match_form": info["match_form"],
                    },
                )
            )

    by_src: dict[str, list[Edge]] = defaultdict(list)
    for e in raw_edges:
        by_src[e.source].append(e)
    out: list[Edge] = []
    for src, lst in by_src.items():
        lst.sort(key=lambda e: (-e.weight, e.target))
        out.extend(lst[: params.refers_top_k])
    return out


# ---------------------------------------------------------------------------
# Layer C — shared_entity
# ---------------------------------------------------------------------------

def _entity_key(surface: str) -> str:
    parts = norm_ru(surface).split()
    if not parts:
        return ""
    parts = [stem_ru(p) for p in parts]
    return " ".join(parts)


def layer_c_shared_entity(
    *,
    docs: list[DocMeta],
    doc_entities: dict[str, list[str]],
    doc_entity_chunks: dict[str, dict[str, list[str]]] | None = None,
    case_stats: dict[str, dict[str, int]] | None = None,
    params: GraphIndexParams | None = None,
) -> list[Edge]:
    """case_stats: token_norm -> {upper: n, lower: n} from corpus scan."""
    params = params or GraphIndexParams()
    doc_entity_chunks = doc_entity_chunks or {}
    case_stats = case_stats or {}
    by_id = {d.document_id: d for d in docs}
    n_works = max(1, len({d.work_key for d in docs}))

    # map entity_key -> display, and work-level df
    work_entities: dict[str, set[str]] = defaultdict(set)
    doc_keys: dict[str, set[str]] = {}
    display: dict[str, str] = {}
    display_count: dict[str, Counter[str]] = defaultdict(Counter)

    for doc_id, ents in doc_entities.items():
        keys: set[str] = set()
        meta = by_id.get(doc_id)
        for e in ents[: params.entity_top_n * 2]:
            cleaned = clean_entity_surface(e)
            if not cleaned:
                continue
            # sentence-initial bias: if mostly lowercase in corpus, drop
            first = cleaned.split()[0]
            st = case_stats.get(norm_ru(first))
            if st:
                low, up = st.get("lower", 0), st.get("upper", 0)
                if low > up * 3 and low + up >= 8:
                    continue
            k = _entity_key(cleaned)
            if len(k) < 3 or k in EDITION_STOP:
                continue
            keys.add(k)
            display_count[k][cleaned] += 1
        doc_keys[doc_id] = keys
        if meta:
            work_entities[meta.work_key] |= keys

    for k, ctr in display_count.items():
        display[k] = ctr.most_common(1)[0][0]

    work_df: Counter[str] = Counter()
    for ents in work_entities.values():
        for e in ents:
            work_df[e] += 1

    max_df = max(2, int(params.entity_max_work_frac * n_works))
    valid = {e for e, c in work_df.items() if 2 <= c <= max_df}

    for doc_id in list(doc_keys.keys()):
        doc_keys[doc_id] = {e for e in doc_keys[doc_id] if e in valid}

    inv: dict[str, list[str]] = defaultdict(list)
    for doc_id, ents in doc_keys.items():
        for e in ents:
            inv[e].append(doc_id)

    pair_common: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e, members in inv.items():
        mem = sorted(set(members))
        for i in range(len(mem)):
            for j in range(i + 1, len(mem)):
                a, b = mem[i], mem[j]
                if same_work(by_id.get(a), by_id.get(b)):
                    continue
                pair_common[(a, b)].add(e)

    def idf(ent: str) -> float:
        return math.log((n_works + 1) / (work_df[ent] + 1)) + 1.0

    neigh: dict[str, list[tuple[float, str, set[str]]]] = defaultdict(list)
    for (a, b), common in pair_common.items():
        if len(common) < params.shared_min_entities:
            continue
        w = sum(idf(e) for e in common)
        neigh[a].append((w, b, common))
        neigh[b].append((w, a, common))

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for src, lst in neigh.items():
        lst.sort(key=lambda x: (-x[0], x[1]))
        for w, tgt, common in lst[: params.shared_top_k]:
            key = tuple(sorted((src, tgt)))
            if key in seen:
                continue
            seen.add(key)
            chunk_ids: list[str] = []
            for ent in sorted(common)[:5]:
                for did in (src, tgt):
                    for cid in (doc_entity_chunks.get(did) or {}).get(ent, [])[:2]:
                        if cid not in chunk_ids:
                            chunk_ids.append(cid)
            edges.append(
                Edge(
                    source=key[0],
                    target=key[1],
                    type="shared_entity",
                    weight=round(float(w), 4),
                    provenance={
                        "entities": [display.get(e, e) for e in sorted(common)[:10]],
                        "chunk_ids": chunk_ids[:3],
                    },
                )
            )
    return edges


# ---------------------------------------------------------------------------
# Layer D — lex_similar (fixed for Russian)
# ---------------------------------------------------------------------------

class LexCandidateCounter:
    def __init__(self) -> None:
        self.comparisons = 0


def layer_d_lex_similar(
    *,
    docs: list[DocMeta],
    doc_term_counts: dict[str, Counter[str]],
    params: GraphIndexParams | None = None,
    counter: LexCandidateCounter | None = None,
) -> tuple[list[Edge], dict[str, Any]]:
    """Return edges + debug stats (score histogram).

    Bug cause (slice 1): scored pairs via product of tiny per-term TF-IDF weights
    with a high absolute threshold, while also filtering terms with df up to 35–60%
    of *documents* (fragments). Rare literary stems rarely co-occurred under that
    product threshold; only tiny English sample files survived. Fix: stem+RU stops,
    document-level vectors with cosine, df bounds on *works*, lower cosine threshold.
    """
    params = params or GraphIndexParams()
    by_id = {d.document_id: d for d in docs}
    n_docs = len(doc_term_counts)
    if n_docs < 2:
        return [], {"scores": [], "bugfix": "n_docs<2"}

    # work-level df for term rarity
    work_terms: dict[str, set[str]] = defaultdict(set)
    for doc_id, counts in doc_term_counts.items():
        meta = by_id.get(doc_id)
        wk = meta.work_key if meta else doc_id
        work_terms[wk] |= set(counts.keys())
    n_works = max(1, len(work_terms))
    df_work: Counter[str] = Counter()
    for terms in work_terms.values():
        for t in terms:
            df_work[t] += 1

    max_df = max(2, int(0.30 * n_works))
    # characteristic vectors per document
    vectors: dict[str, dict[str, float]] = {}
    inv: dict[str, list[str]] = defaultdict(list)
    for doc_id, counts in doc_term_counts.items():
        total = sum(counts.values()) or 1
        scored: list[tuple[float, str]] = []
        for t, c in counts.items():
            dw = df_work.get(t, 0)
            if dw <= 1 or dw > max_df:
                continue
            tf = c / total
            idf = math.log((n_works + 1) / (dw + 1)) + 1.0
            scored.append((tf * idf, t))
        scored.sort(reverse=True)
        top = scored[: params.lex_top_m]
        vec = {t: s for s, t in top}
        if not vec:
            continue
        # L2 normalize for cosine
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors[doc_id] = {t: v / norm for t, v in vec.items()}
        for t in vectors[doc_id]:
            inv[t].append(doc_id)

    pair_best: dict[tuple[str, str], tuple[float, list[str]]] = {}
    for t, members in inv.items():
        # rare-ish terms drive candidates
        if df_work.get(t, 0) > max(3, int(0.20 * n_works)):
            continue
        mem = sorted(set(members))
        for i in range(len(mem)):
            for j in range(i + 1, len(mem)):
                a, b = mem[i], mem[j]
                if same_work(by_id.get(a), by_id.get(b)):
                    continue
                if counter is not None:
                    counter.comparisons += 1
                va, vb = vectors.get(a), vectors.get(b)
                if not va or not vb:
                    continue
                shared = set(va) & set(vb)
                if len(shared) < 2:
                    continue
                score = sum(va[x] * vb[x] for x in shared)
                # soft complete cosine over union already L2-normed partial
                key = (a, b) if a < b else (b, a)
                prev = pair_best.get(key)
                if not prev or score > prev[0]:
                    pair_best[key] = (score, sorted(shared)[:5])

    scores = [s for s, _ in pair_best.values()]
    neigh: dict[str, list[tuple[float, str, list[str]]]] = defaultdict(list)
    for (a, b), (score, terms) in pair_best.items():
        if score < params.lex_min_score:
            continue
        neigh[a].append((score, b, terms))
        neigh[b].append((score, a, terms))

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for src, lst in neigh.items():
        lst.sort(key=lambda x: (-x[0], x[1]))
        for score, tgt, terms in lst[: params.lex_top_k]:
            key = tuple(sorted((src, tgt)))
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                Edge(
                    source=key[0],
                    target=key[1],
                    type="lex_similar",
                    weight=round(float(score), 6),
                    provenance={"terms": terms},
                )
            )

    # histogram
    hist = Counter()
    for s in scores:
        bucket = round(s, 2)
        hist[bucket] += 1
    debug = {
        "bugfix": (
            "Previously used product-of-tiny-TFIDF with document-fragment df caps; "
            "RU terms never cleared the absolute threshold except tiny English samples."
        ),
        "score_count": len(scores),
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "score_hist": dict(sorted(hist.items())[:40]),
        "edges": len(edges),
        "threshold": params.lex_min_score,
        "comparisons": counter.comparisons if counter else None,
    }
    return edges, debug


# ---------------------------------------------------------------------------
# Layer E — co_cited
# ---------------------------------------------------------------------------

_SMOKE_TITLE_RE = re.compile(r"(^ping$|smoke$|^eval\b|quality)", re.I)


def is_eval_chat_session(title: str | None) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    return bool(_SMOKE_TITLE_RE.search(t))


def extract_doc_ids_from_citations(citations: Any) -> list[str]:
    out: list[str] = []
    if not citations:
        return out
    if isinstance(citations, list):
        for item in citations:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                for k in ("document_id", "doc_id", "id"):
                    if item.get(k):
                        out.append(str(item[k]))
                        break
    return out


def layer_e_co_cited(
    citation_lists: Iterable[tuple[list[str], bool]],
    *,
    include_eval: bool = False,
) -> list[Edge]:
    """citation_lists: (doc_ids, is_eval)."""
    live: Counter[tuple[str, str]] = Counter()
    ev: Counter[tuple[str, str]] = Counter()
    for cites, is_eval in citation_lists:
        ids = sorted({str(x) for x in cites if x})
        bucket = ev if is_eval else live
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                bucket[(ids[i], ids[j])] += 1
    edges: list[Edge] = []
    for (a, b), w in live.items():
        edges.append(
            Edge(
                source=a,
                target=b,
                type="co_cited",
                weight=float(w),
                provenance={"co_citation_answers": w, "channel": "live"},
            )
        )
    if include_eval:
        for (a, b), w in ev.items():
            edges.append(
                Edge(
                    source=a,
                    target=b,
                    type="co_cited_eval",
                    weight=float(w) * 0.5,
                    provenance={"co_citation_answers": w, "channel": "eval"},
                )
            )
    return edges


def aggregate_work_edges(
    docs: list[DocMeta],
    edges: list[Edge],
    *,
    top_k: int = DEFAULT_WORK_NEIGHBOR_TOP_K,
) -> list[Edge]:
    """Collapse doc↔doc textual edges into work↔work.

    Weight uses ``log1p(sum_w) / sqrt(frag_a * frag_b)`` so long novels do not
    dominate purely by fragment count. ``mentions_author`` aggregates to
    work → author.
    """
    by_id = {d.document_id: d for d in docs}
    frag_count: Counter[str] = Counter(d.work_key for d in docs)
    textual = {"refers_to", "shared_entity", "lex_similar", "co_cited", "co_cited_eval"}
    acc: dict[tuple[str, str, str], dict[str, Any]] = {}
    author_acc: dict[tuple[str, str], dict[str, Any]] = {}

    for e in edges:
        if e.type == "mentions_author":
            sa = by_id.get(e.source)
            if not sa or not str(e.target).startswith("author:"):
                continue
            key = (sa.work_key, e.target)
            slot = author_acc.setdefault(key, {"weight": 0.0, "examples": [], "chunks": 0})
            slot["weight"] += float(e.weight)
            slot["chunks"] += int(e.weight)
            if len(slot["examples"]) < 5:
                slot["examples"].append(
                    {
                        "doc": e.source,
                        "weight": e.weight,
                        "provenance": e.provenance,
                    }
                )
            continue
        if e.type not in textual:
            continue
        sa, sb = by_id.get(e.source), by_id.get(e.target)
        if not sa or not sb or same_work(sa, sb):
            continue
        wa, wb = sa.work_key, sb.work_key
        if wa > wb:
            wa, wb = wb, wa
        key = (wa, wb, e.type)
        slot = acc.setdefault(key, {"weight": 0.0, "examples": []})
        slot["weight"] += float(e.weight)
        if len(slot["examples"]) < 5:
            slot["examples"].append(
                {
                    "doc_a": e.source,
                    "doc_b": e.target,
                    "weight": e.weight,
                    "provenance": e.provenance,
                }
            )

    candidates: list[Edge] = []
    for (wa, wb, typ), slot in acc.items():
        fa = max(1, frag_count.get(wa, 1))
        fb = max(1, frag_count.get(wb, 1))
        # log1p(sum) / sqrt(frags) — see docstring
        w = math.log1p(float(slot["weight"])) / math.sqrt(fa * fb)
        candidates.append(
            Edge(
                source=f"work:{wa}",
                target=f"work:{wb}",
                type=f"work_{typ}",
                weight=round(w, 4),
                provenance={
                    "examples": slot["examples"],
                    "from_docs": True,
                    "raw_sum": round(float(slot["weight"]), 4),
                    "fragment_count_a": fa,
                    "fragment_count_b": fb,
                    "agg": "log1p_sum_over_sqrt_frags",
                },
            )
        )

    by_work_type: dict[tuple[str, str], list[Edge]] = defaultdict(list)
    for e in candidates:
        typ = e.type
        by_work_type[(e.source, typ)].append(e)
        by_work_type[(e.target, typ)].append(e)
    kept: set[int] = set()
    for lst in by_work_type.values():
        lst.sort(key=lambda e: (-e.weight, e.source, e.target))
        for e in lst[:top_k]:
            kept.add(id(e))
    out = [e for e in candidates if id(e) in kept]

    for (wk, author_node), slot in author_acc.items():
        out.append(
            Edge(
                source=f"work:{wk}",
                target=author_node,
                type="work_mentions_author",
                weight=float(slot["chunks"]),
                provenance={
                    "examples": slot["examples"],
                    "chunk_mentions": slot["chunks"],
                },
            )
        )
    return out


# ---------------------------------------------------------------------------
# Assemble + cache + build
# ---------------------------------------------------------------------------

def assemble_index(
    *,
    tenant_id: str,
    fingerprint: str,
    docs: list[DocMeta],
    edges: list[Edge],
    params: GraphIndexParams,
    built_ms: int,
    extra_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    edge_counts: Counter[str] = Counter(e.type for e in edges)
    content_linked: set[str] = set()
    for e in edges:
        if e.type in (
            "refers_to",
            "shared_entity",
            "lex_similar",
            "co_cited",
            "co_cited_eval",
            "mentions_author",
        ):
            content_linked.add(e.source)
            if not str(e.target).startswith("author:"):
                content_linked.add(e.target)
    isolated_docs = [d.document_id for d in docs if d.document_id not in content_linked]
    works = {d.work_key: d for d in docs}
    work_linked: set[str] = set()
    for e in edges:
        if e.type.startswith("work_") and e.type != "work_meta":
            work_linked.add(e.source.removeprefix("work:"))
            work_linked.add(e.target.removeprefix("work:"))
        if e.type in ("refers_to", "shared_entity", "lex_similar", "co_cited", "co_cited_eval"):
            sa = next((d for d in docs if d.document_id == e.source), None)
            sb = next((d for d in docs if d.document_id == e.target), None)
            if sa:
                work_linked.add(sa.work_key)
            if sb:
                work_linked.add(sb.work_key)
    isolated_works = [wk for wk in works if wk not in work_linked]
    no_author = [d.document_id for d in docs if not d.author_key]
    frag_docs = [d.document_id for d in docs if d.fragment_no is not None]
    work_frag: Counter[str] = Counter(d.work_key for d in docs)

    themes = sorted({d.theme for d in docs})
    authors = sorted({d.author_key for d in docs if d.author_key})
    stats = {
        "documents": len(docs),
        "works": len(works),
        "themes": len(themes),
        "authors": len(authors),
        "theme_list": themes,
        "author_list": authors,
        "edge_counts_by_type": dict(edge_counts),
        "isolated_documents": len(isolated_docs),
        "isolated_document_ids_sample": isolated_docs[:20],
        "isolated_works": len(isolated_works),
        "isolated_work_share": round(len(isolated_works) / max(1, len(works)), 4),
        "fragment_documents": len(frag_docs),
        "documents_without_author": len(no_author),
        "documents_without_author_sample": no_author[:20],
        "top_works_by_fragments": [
            {"work_key": wk, "fragment_count": n, "title": works[wk].title, "author_key": works[wk].author_key}
            for wk, n in work_frag.most_common(20)
        ],
    }
    if extra_stats:
        stats.update(extra_stats)
    return {
        "version": INDEX_VERSION,
        "tenant_id": tenant_id,
        "fingerprint": fingerprint,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "built_ms": built_ms,
        "params": params.to_dict(),
        "documents": [
            {
                "document_id": d.document_id,
                "filename": d.filename,
                "theme": d.theme,
                "pack": d.pack,
                "source": d.source,
                "author_key": d.author_key,
                "author_ru": d.author_ru,
                "title": d.title,
                "fragment_no": d.fragment_no,
                "work_key": d.work_key,
                "chunk_count": d.chunk_count,
                "created_at": d.created_at,
            }
            for d in docs
        ],
        "edges": [e.to_dict() for e in edges],
        "stats": stats,
    }


def graph_dir() -> Path:
    p = data_root() / "graph"
    p.mkdir(parents=True, exist_ok=True)
    return p


def index_path(tenant_id: UUID | str, fingerprint: str) -> Path:
    return graph_dir() / f"{tenant_id}_{fingerprint}_index.json"


def purge_old_indexes(tenant_id: UUID | str, keep_fingerprint: str) -> int:
    removed = 0
    for p in graph_dir().glob(f"{tenant_id}_*_index.json"):
        name = p.name
        mid = name[len(f"{tenant_id}_") : -len("_index.json")]
        if mid != keep_fingerprint:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def write_index_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_index(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_cached_index(db: Session, tenant_id: UUID) -> dict[str, Any] | None:
    fp = corpus_fingerprint(db, tenant_id)
    return read_index(index_path(tenant_id, fp))


def _active_documents(db: Session, tenant_id: UUID) -> list[tuple[Document, DocumentVersion]]:
    return list(
        db.query(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.id == Document.active_version_id)
        .filter(
            Document.tenant_id == tenant_id,
            Document.deleted_at.is_(None),
            Document.status != "deleted",
            Document.active_version_id.isnot(None),
        )
        .order_by(Document.created_at.asc())
        .all()
    )


def _iter_chunks_batched(
    db: Session,
    tenant_id: UUID,
    version_ids: list[UUID],
    batch_size: int,
) -> Iterable[tuple[str, str, str]]:
    if not version_ids:
        return
    for i in range(0, len(version_ids), batch_size):
        batch = version_ids[i : i + batch_size]
        rows = (
            db.query(Chunk.document_id, Chunk.id, Chunk.text)
            .filter(Chunk.tenant_id == tenant_id, Chunk.version_id.in_(batch))
            .order_by(Chunk.document_id, Chunk.ordinal)
            .all()
        )
        for doc_id, chunk_id, text in rows:
            yield str(doc_id), str(chunk_id), text or ""


def build_index_for_tenant(
    db: Session,
    tenant_id: UUID,
    *,
    params: GraphIndexParams | None = None,
    force: bool = False,
) -> dict[str, Any]:
    params = params or GraphIndexParams()
    t0 = time.perf_counter()
    fp = corpus_fingerprint(db, tenant_id)
    path = index_path(tenant_id, fp)
    if not force:
        cached = read_index(path)
        if cached and cached.get("fingerprint") == fp and cached.get("version") == INDEX_VERSION:
            log.info("graph_index_cache_hit", tenant_id=str(tenant_id), fingerprint=fp)
            return cached

    rows = _active_documents(db, tenant_id)
    docs: list[DocMeta] = []
    version_ids: list[UUID] = []
    for doc, ver in rows:
        created = doc.created_at.isoformat() if doc.created_at else None
        docs.append(
            parse_doc_meta(
                document_id=str(doc.id),
                filename=ver.original_name,
                chunk_count=int(ver.indexed_chunk_count or 0),
                created_at=created,
            )
        )
        version_ids.append(ver.id)
    resolve_work_keys(docs)

    edges: list[Edge] = []

    doc_entities: dict[str, list[str]] = defaultdict(list)
    doc_entity_chunks: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    doc_term_counts: dict[str, Counter[str]] = defaultdict(Counter)
    case_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"upper": 0, "lower": 0})
    chunk_rows: list[tuple[str, str, str]] = []
    chunk_total = 0
    clean_chars_removed = 0
    clean_chunks_touched = 0
    clean_examples: list[dict[str, str]] = []
    by_id = {d.document_id: d for d in docs}
    # ordinals per doc for front-matter strip
    ordinal_by_doc: dict[str, int] = defaultdict(int)

    for doc_id, chunk_id, text in _iter_chunks_batched(db, tenant_id, version_ids, params.chunk_batch):
        meta = by_id.get(doc_id)
        ord_n = ordinal_by_doc[doc_id]
        ordinal_by_doc[doc_id] = ord_n + 1
        clean, hdr = clean_chunk_for_graph(
            text or "",
            chunk_ordinal=ord_n,
            doc_meta={
                "author_key": meta.author_key if meta else None,
                "author_ru": meta.author_ru if meta else None,
                "title": meta.title if meta else None,
            },
        )
        removed = int(hdr.get("chars_removed") or 0)
        if removed > 0:
            clean_chars_removed += removed
            clean_chunks_touched += 1
            if len(clean_examples) < 5:
                clean_examples.append(
                    {
                        "doc_id": doc_id,
                        "before": (text or "")[:220],
                        "after": clean[:220],
                    }
                )
            # author from ingest header for authorless docs
            if meta and not meta.author_key and hdr.get("author"):
                ak = str(hdr["author"]).strip()
                if ak and norm_ru(ak) not in LANGUAGE_MARKERS:
                    # map RU alias or keep Latin key
                    mapped = None
                    for ru, key in qi.AUTHOR_ALIASES.items():
                        if norm_ru(ak) == ru or ak == key:
                            mapped = key
                            break
                    meta.author_key = mapped or (ak if re.fullmatch(r"[A-Z][A-Za-z\-]+", ak) else None)
                    if meta.author_key:
                        meta.author_ru = _AUTHOR_RU_BY_KEY.get(meta.author_key)
                        meta.work_key = work_key_for(
                            pack=meta.pack, author_key=meta.author_key, title=meta.title
                        )
        chunk_rows.append((doc_id, chunk_id, clean))
        chunk_total += 1
        for m in re.finditer(r"[A-Za-zА-Яа-яЁё]{3,}", clean, flags=re.UNICODE):
            w = m.group(0)
            key = norm_ru(w)
            if w[0].isupper():
                case_stats[key]["upper"] += 1
            else:
                case_stats[key]["lower"] += 1
        ents = extract_entity_candidates([clean], limit=params.entity_top_n)
        for e in ents:
            cleaned = clean_entity_surface(e) or e
            k = _entity_key(cleaned)
            if cleaned not in doc_entities[doc_id]:
                doc_entities[doc_id].append(cleaned)
            if chunk_id not in doc_entity_chunks[doc_id][k]:
                doc_entity_chunks[doc_id][k].append(chunk_id)
        for tok in tokenize_lex(clean):
            doc_term_counts[doc_id][tok] += 1

    counts_by_doc = Counter(d for d, _, _ in chunk_rows)
    for d in docs:
        d.chunk_count = int(counts_by_doc.get(d.document_id, d.chunk_count))
    resolve_work_keys(docs)

    edges.extend(layer_a_edges(docs))
    edges.extend(layer_mentions_author(docs=docs, chunk_iter=chunk_rows))
    edges.extend(layer_b_refers_to(docs=docs, chunk_iter=chunk_rows, params=params))
    edges.extend(
        layer_c_shared_entity(
            docs=docs,
            doc_entities=dict(doc_entities),
            doc_entity_chunks={k: dict(v) for k, v in doc_entity_chunks.items()},
            case_stats={k: dict(v) for k, v in case_stats.items()},
            params=params,
        )
    )
    lex_edges, lex_debug = layer_d_lex_similar(
        docs=docs, doc_term_counts=dict(doc_term_counts), params=params, counter=LexCandidateCounter()
    )
    edges.extend(lex_edges)

    cite_lists: list[tuple[list[str], bool]] = []
    msgs = (
        db.query(ChatMessage.citations, ChatSession.title)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .filter(ChatMessage.tenant_id == tenant_id, ChatMessage.role == "assistant")
        .all()
    )
    for citations, title in msgs:
        ids = extract_doc_ids_from_citations(citations)
        if len(ids) >= 2:
            cite_lists.append((ids, is_eval_chat_session(title)))
    edges.extend(layer_e_co_cited(cite_lists, include_eval=params.include_eval_cocited))
    edges.extend(aggregate_work_edges(docs, edges, top_k=params.work_neighbor_top_k))

    built_ms = int((time.perf_counter() - t0) * 1000)
    payload = assemble_index(
        tenant_id=str(tenant_id),
        fingerprint=fp,
        docs=docs,
        edges=edges,
        params=params,
        built_ms=built_ms,
        extra_stats={
            "lex_debug": lex_debug,
            "source_tokens": sorted(SOURCE_TOKENS),
            "text_clean": {
                "chars_removed": clean_chars_removed,
                "chunks_touched": clean_chunks_touched,
                "examples": clean_examples,
            },
        },
    )
    write_index_atomic(path, payload)
    removed = purge_old_indexes(tenant_id, fp)
    stats = payload["stats"]
    log.info(
        "graph_index_built",
        tenant_id=str(tenant_id),
        fingerprint=fp,
        built_ms=built_ms,
        documents=stats["documents"],
        works=stats["works"],
        chunks=chunk_total,
        edge_counts=stats["edge_counts_by_type"],
        isolated_docs=stats["isolated_documents"],
        isolated_works=stats["isolated_works"],
        purged_old=removed,
    )
    return payload


def neighbors_for_document(
    index: dict[str, Any],
    document_id: str,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    doc_id = str(document_id)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in index.get("edges") or []:
        src, tgt = e.get("source"), e.get("target")
        if src == doc_id:
            other = tgt
        elif tgt == doc_id:
            other = src
        else:
            continue
        grouped[e.get("type") or "unknown"].append(
            {
                "neighbor_id": other,
                "weight": e.get("weight"),
                "provenance": e.get("provenance") or {},
                "direction": "out" if src == doc_id else "in",
            }
        )
    out: dict[str, list[dict[str, Any]]] = {}
    for typ, items in grouped.items():
        items.sort(key=lambda x: (-float(x.get("weight") or 0), str(x.get("neighbor_id"))))
        out[typ] = items[:limit]
    return {"document_id": doc_id, "neighbors_by_type": out, "fingerprint": index.get("fingerprint")}
