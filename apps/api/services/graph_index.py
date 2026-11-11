"""Lexical document connectivity index (no Qdrant, no embeddings, no UI).

Offline + corpus lifecycle (1d): per-doc artifacts, full|incremental rebuild,
cache key = tenant + fingerprint + index_version + params_hash.
Language rules live in configs/graph_lexicon.json — not corpus-specific facts.
"""

from __future__ import annotations

import json
import math
import re
import threading
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
from apps.api.services import graph_index_cache as gcache
from apps.api.services.graph_lexicon import frozenset_field, tuple_field
from apps.api.services.quality_state import corpus_fingerprint
from apps.api.services.storage import data_root
from apps.api.services.graph_text_clean import clean_chunk_for_graph
from apps.api.services import graph_refers as grefer
from apps.api.services import graph_authors as gauth
from apps.api.services import graph_duplicates as gdup
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

INDEX_VERSION = 9

# Filename grammar tokens (source-site / language folder markers), not corpus content.
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

def _genre_markers() -> tuple[str, ...]:
    return tuple_field("genre_markers") or (
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


def _genre_title_blocklist() -> frozenset[str]:
    return frozenset_field("genre_title_blocklist") or frozenset(
        {"рассказы", "стихотворения", "поэмы", "повести", "пьесы", "романы"}
    )


# Back-compat names used across layers (resolved from editable lexicon).
REFERS_GENRE_MARKERS = _genre_markers()
_AUTHOR_FIRST_NAMES = tuple_field("author_first_names") or (
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
PLAY_STAGE_WORDS = frozenset_field("play_stage_words")
PLACE_LEMMAS = frozenset_field("place_lemmas")
GOD_FORMS = frozenset_field("god_forms") or frozenset(
    {"бог", "бога", "богу", "богом", "божий", "божья", "божье", "божие", "боже"}
)
_DIMINUTIVE_NAMES = frozenset_field("diminutive_names")
_DIMINUTIVE_SUFFIXES = tuple_field("diminutive_suffixes") or (
    "ша",
    "шка",
    "нька",
    "енька",
    "ушка",
    "юша",
    "юшка",
)
_EDITORIAL_STOP = frozenset_field("editorial_stop_terms")
_SPEECH_VERBS = frozenset(
    stem_ru(v) for v in (tuple_field("speech_verbs") or ())
) | frozenset(norm_ru(v) for v in (tuple_field("speech_verbs") or ()))
_ENTITY_INTERJECTIONS = frozenset_field("entity_interjections") or frozenset(
    {"господи", "извините", "ей-богу", "ейбогу", "простите", "пожалуйста"}
)
_RELIGIOUS_WEAK = frozenset_field("god_forms") | frozenset(
    {"бог", "господь", "христос", "евангелие", "господи"}
)

_PATRONYMIC_RE = re.compile(
    r"(?:ович|евич|овна|евна|ична|инична)$",
    re.UNICODE,
)
_SURNAME_RE = re.compile(
    r"(?:ов|ев|ёв|ин|ын|ский|ская|цкая|енко|дзе|швили)$",
    re.UNICODE,
)
_PRONOUN_PARTICLE_RE = re.compile(
    r"(?:^|-)(?:то|нибудь|либо)$",
    re.UNICODE,
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
    part_no: int | None = None
    volume_no: int | None = None
    work_key: str = ""
    chunk_count: int = 0
    created_at: str | None = None
    author_source: str | None = None  # filename | ingest_header | text_header | work_merge
    canonical: bool = True
    duplicate_of: str | None = None


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


_PART_VOL_RE = re.compile(
    r"(?i)(?:^|[\s_\-–—]+)"
    r"(?:"
    r"(?:часть|ч\.?)\s*(?P<part>\d+)"
    r"|(?:том|т\.?)\s*(?P<vol>\d+)"
    r"|(?:книга|кн\.?)\s*(?P<book>\d+)"
    r")\s*$"
)


def normalize_work_title(title: str) -> tuple[str, int | None, int | None]:
    """Strip part/volume suffixes; ё→е; return (core_title, part_no, volume_no)."""
    t = (title or "").replace("ё", "е").replace("Ё", "Е")
    t = re.sub(r"\s+", " ", t).strip(" ._")
    part_no: int | None = None
    volume_no: int | None = None
    # Repeatedly strip trailing part/volume markers (also underscore form)
    for _ in range(4):
        m = _PART_VOL_RE.search(t.replace("_", " "))
        if not m:
            # underscore variants: "Том_2", "-_Часть_3"
            m2 = re.search(
                r"(?:^|[\s_\-–—]+)(?:часть|ч)_?(?P<part>\d+)\s*$|"
                r"(?:^|[\s_\-–—]+)(?:том|т)_?(?P<vol>\d+)\s*$|"
                r"(?:^|[\s_\-–—]+)(?:книга|кн)_?(?P<book>\d+)\s*$",
                t,
                flags=re.IGNORECASE,
            )
            if not m2:
                break
            m = m2
        if m.groupdict().get("part"):
            part_no = int(m.group("part"))
        if m.groupdict().get("vol"):
            volume_no = int(m.group("vol"))
        if m.groupdict().get("book"):
            part_no = int(m.group("book"))
        t = t[: m.start()].rstrip(" -_–—")
    t = re.sub(r"\s+", " ", t).strip(" ._")
    return t or (title or "untitled"), part_no, volume_no


def work_key_for(*, pack: str, author_key: str | None, title: str) -> str:
    a = (author_key or "").strip() or "_"
    core, _, _ = normalize_work_title(title)
    t = re.sub(r"\s+", " ", norm_ru(core)).strip()
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
    core, part_no, volume_no = normalize_work_title(parsed["title"])
    wk = work_key_for(pack=parsed["pack"], author_key=parsed["author_key"], title=core)
    return DocMeta(
        document_id=str(document_id),
        filename=filename,
        theme=parsed["theme"],
        pack=parsed["pack"],
        source=parsed["source"],
        author_key=parsed["author_key"],
        author_ru=parsed["author_ru"],
        title=core,
        fragment_no=parsed["fragment_no"],
        part_no=part_no,
        volume_no=volume_no,
        work_key=wk,
        chunk_count=chunk_count,
        created_at=created_at,
        author_source="filename" if parsed["author_key"] else None,
    )


def resolve_work_keys(docs: list[DocMeta]) -> None:
    """Merge parts/volumes and authorless unique titles into one work_key."""
    # Recompute keys from normalized titles
    for d in docs:
        core, part_no, volume_no = normalize_work_title(d.title)
        d.title = core
        if part_no is not None:
            d.part_no = part_no
        if volume_no is not None:
            d.volume_no = volume_no
        d.work_key = work_key_for(pack=d.pack, author_key=d.author_key, title=d.title)

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
        d.author_key = ak
        d.author_ru = _AUTHOR_RU_BY_KEY.get(ak)
        d.author_source = d.author_source or "work_merge"
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
    while j >= 0 and text[j] in " \t«\"“„'":
        j -= 1
    if j < 0:
        return True
    if text[j] in ".!?\n":
        return True
    # Dialogue dash forms: "— Word", "-- Word", "– Word"
    if text[j] in "—–":
        return True
    if text[j] == "-" and (j == 0 or text[j - 1] in "\n \t-"):
        return True
    return False


def _window(text: str, idx: int, length: int, radius: int = 48) -> str:
    a = max(0, idx - radius)
    b = min(len(text), idx + length + radius)
    return text[a:b]


def _context_snip(text: str, idx: int, phrase_len: int, width: int = 120) -> str:
    a = max(0, idx - width // 3)
    b = min(len(text), idx + phrase_len + width // 2)
    return re.sub(r"\s+", " ", text[a:b]).strip()


# refers helpers live in graph_refers (strict A1 matching)


def find_author_mentions_in_text(raw: str) -> list[tuple[str, str, int, int]]:
    """Back-compat wrapper → (author_key, surface, start, end)."""
    return [
        (h["author_key"], h["surface"], h["start"], h["end"])
        for h in gauth.find_author_mentions(raw)
    ]


def layer_mentions_author(
    *,
    docs: list[DocMeta],
    chunk_iter: Iterable[tuple[str, str, str]],
) -> list[Edge]:
    """document → author:<key> with confidence + zone weighting."""
    by_id = {d.document_id: d for d in docs}
    hits: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "chunks": [],
                "snips": [],
                "weight_sum": 0.0,
                "confidence": "medium",
                "zone": "body",
            }
        )
    )
    for doc_id, chunk_id, text in chunk_iter:
        raw = text or ""
        src = by_id.get(doc_id)
        for h in gauth.find_author_mentions(raw):
            key = h["author_key"]
            if src and src.author_key and key == src.author_key:
                continue
            zone = grefer.detect_zone(raw, h["start"])
            conf = h.get("confidence") or "medium"
            w = 1.0
            if conf == "medium":
                w *= 0.7
            if zone == "commentary":
                w *= 0.3
            bucket = hits[doc_id][key]
            if chunk_id not in bucket["chunks"]:
                bucket["chunks"].append(chunk_id)
                bucket["weight_sum"] += w
            if conf == "high":
                bucket["confidence"] = "high"
            if zone == "commentary" and bucket["zone"] != "body":
                bucket["zone"] = "commentary"
            elif zone == "commentary" and not bucket["snips"]:
                bucket["zone"] = "commentary"
            # prefer body label unless all commentary
            if zone == "commentary":
                bucket.setdefault("zones", []).append("commentary")
            else:
                bucket.setdefault("zones", []).append("body")
            if len(bucket["snips"]) < 3:
                bucket["snips"].append(_context_snip(raw, h["start"], h["end"] - h["start"]))

    edges: list[Edge] = []
    for doc_id, by_auth in hits.items():
        for key, info in by_auth.items():
            zones = info.get("zones") or ["body"]
            zone = "commentary" if zones and all(z == "commentary" for z in zones) else (
                "commentary" if zones.count("commentary") > zones.count("body") else "body"
            )
            # if any commentary, expose it; mixed → body unless only commentary
            if "commentary" in zones and "body" not in zones:
                zone = "commentary"
            elif "commentary" in zones:
                zone = "mixed"
            else:
                zone = "body"
            edges.append(
                Edge(
                    source=doc_id,
                    target=f"author:{key}",
                    type="mentions_author",
                    weight=round(float(info["weight_sum"]), 4),
                    provenance={
                        "author_key": key,
                        "chunk_ids": info["chunks"][:5],
                        "context": (info["snips"][0] if info["snips"] else "")[:160],
                        "contexts": info["snips"][:3],
                        "confidence": info.get("confidence") or "medium",
                        "zone": zone,
                    },
                )
            )
    return edges



def build_refers_phrase_index(docs: list[DocMeta]) -> list[tuple[str, str, str, bool]]:
    """Compatibility wrapper — full-title phrases only (A1)."""
    return grefer.build_title_phrases(docs)


def layer_b_refers_to(
    *,
    docs: list[DocMeta],
    chunk_iter: Iterable[tuple[str, str, str]],
    params: GraphIndexParams | None = None,
    phrase_work_df: dict[str, int] | None = None,
) -> list[Edge]:
    """refers_to with strict full-title matching (A1)."""
    params = params or GraphIndexParams()
    by_id = {d.document_id: d for d in docs}
    phrases = grefer.build_title_phrases(docs)
    if not phrases:
        return []

    chunk_list = list(chunk_iter)
    lower_df = grefer.lower_token_df(chunk_list)
    frequent = grefer.frequent_title_norms(
        phrases, lower_df, n_docs=len(docs), top_frac=0.20
    )

    # Index by first token + its stem (inflected first words in text)
    by_first: dict[str, list[tuple[str, str, str, bool]]] = defaultdict(list)
    for item in phrases:
        first = item[0].split()[0] if item[0] else item[0]
        by_first[first].append(item)
        st = stem_ru(first)
        if st and st != first:
            by_first[st].append(item)

    hits: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "chunks": [],
                "phrase": "",
                "multi": False,
                "snips": [],
                "match_form": "",
                "zone": "body",
                "weight_sum": 0.0,
            }
        )
    )

    for doc_id, chunk_id, text in chunk_list:
        raw = text or ""
        if not raw.strip():
            continue
        src_meta = by_id.get(doc_id)
        tok_spans = grefer._tokens_with_spans(raw)
        tokens_present = {t[0] for t in tok_spans}
        first_index: dict[str, list[int]] = defaultdict(list)
        for i, (n, _a, _b, _s) in enumerate(tok_spans):
            first_index[n].append(i)
            st = stem_ru(n)
            if st != n:
                first_index[st].append(i)
        quoted_spans = grefer._exact_quoted_spans(raw)
        zone_cut = grefer.commentary_cut(raw)
        has_quotes = bool(quoted_spans)
        genre_present = bool(tokens_present & set(grefer.genre_markers()))
        # Only capitalized tokens can start a title hit (lowercase never counts)
        cap_norms = {
            t[0]
            for t in tok_spans
            if t[3] and t[3][0].isupper()
        }
        candidates: list[tuple[str, str, str, bool]] = []
        seed_toks = set(cap_norms)
        if has_quotes:
            # quotes may contain titles whose first word isn't capitalized outside
            for _a, _b, inner in quoted_spans:
                seed_toks.update(grefer.title_tokens(inner)[:1])
        for tok in seed_toks:
            candidates.extend(by_first.get(tok) or [])
            st = stem_ru(tok)
            if st != tok:
                candidates.extend(by_first.get(st) or [])
        seen_c: set[tuple[str, str]] = set()
        uniq: list[tuple[str, str, str, bool]] = []
        for item in sorted(candidates, key=lambda x: -len(x[0])):
            key = (item[0], item[1])
            if key in seen_c:
                continue
            seen_c.add(key)
            uniq.append(item)

        matched_spans: list[tuple[int, int]] = []
        for phrase_n, target_id, surface, multi in uniq:
            # One-word titles only accept quoted|genre — skip chunks with neither
            if not multi and not has_quotes and not genre_present:
                continue
            if target_id == doc_id:
                continue
            tgt_meta = by_id.get(target_id)
            if same_work(src_meta, tgt_meta):
                continue
            found = grefer.find_title_hits(
                raw,
                surface,
                tokens=tok_spans,
                quoted_spans=quoted_spans if has_quotes else [],
                zone_cut=zone_cut,
                first_index=first_index,
            )
            if not found:
                continue
            same_author = bool(
                src_meta
                and tgt_meta
                and src_meta.author_key
                and tgt_meta.author_key
                and src_meta.author_key == tgt_meta.author_key
            )
            freq = phrase_n in frequent
            accepted = False
            best = None
            for hit in found:
                if any(
                    not (hit["end"] <= a or hit["start"] >= b)
                    for a, b in matched_spans
                ):
                    continue
                form = grefer.classify_hit(
                    raw,
                    hit,
                    author_key=tgt_meta.author_key if tgt_meta else None,
                    same_author=same_author,
                    frequent_title=freq,
                )
                if form:
                    hit = dict(hit)
                    hit["match_form"] = form
                    accepted = True
                    best = hit
                    break
            if not accepted or best is None:
                continue
            matched_spans.append((best["start"], best["end"]))
            zone = best.get("zone") or grefer.detect_zone(raw, best["start"])
            w = 0.3 if zone == "commentary" else 1.0
            bucket = hits[doc_id][target_id]
            if chunk_id not in bucket["chunks"]:
                bucket["chunks"].append(chunk_id)
                bucket["weight_sum"] += w
            if not bucket["phrase"] or (multi and not bucket["multi"]):
                bucket["phrase"] = surface
                bucket["multi"] = bool(multi)
                bucket["match_form"] = best["match_form"]
                bucket["zone"] = zone
            elif not bucket["match_form"]:
                bucket["match_form"] = best["match_form"]
            if len(bucket["snips"]) < 2:
                bucket["snips"].append(
                    grefer.context_snip(raw, best["start"], best["end"])
                )

    raw_edges: list[Edge] = []
    for src, by_tgt in hits.items():
        for tgt, info in by_tgt.items():
            w = float(info["weight_sum"])
            if w <= 0:
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
                        "multiword": bool(info["multi"]),
                        "match_form": info["match_form"],
                        "zone": info.get("zone") or "body",
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


def stem_entity_token(token: str) -> str:
    """Aggressive stem for entity lemma grouping (Анна/Анной → анн)."""
    n = norm_ru(token)
    if not n:
        return ""
    if n in GOD_FORMS or n in _RELIGIOUS_WEAK:
        return "бог" if n.startswith("бог") or n in {"боже", "божий", "божья", "божье", "божие"} else (
            "господь" if n.startswith("господ") else (
                "христос" if n.startswith("христ") else (
                    "евангелие" if n.startswith("евангел") else stem_ru(n)
                )
            )
        )
    if len(n) < 4:
        return n
    for end in (
        "ами", "ями", "ого", "ему", "ому", "ыми", "ими",
        "ах", "ях", "ов", "ев", "ей", "ом", "ем", "ам", "ям",
        "ую", "юю", "ая", "яя", "ые", "ие", "ых", "их",
        "ой", "ий", "ый", "а", "я", "у", "ю", "е", "ы", "и", "о",
    ):
        if n.endswith(end) and len(n) - len(end) >= 3:
            return n[: len(n) - len(end)]
    return n


def _entity_key(surface: str) -> str:
    parts = norm_ru(surface).split()
    if not parts:
        return ""
    if len(parts) == 1 and (parts[0] in GOD_FORMS or parts[0] in _RELIGIOUS_WEAK):
        return stem_entity_token(parts[0])
    return " ".join(stem_entity_token(p) for p in parts)


def entity_kind(surface: str, key: str) -> str:
    n = norm_ru(surface)
    if key in {"бог", "господь", "христос", "евангелие"} or n in GOD_FORMS:
        return "religious"
    if n in PLACE_LEMMAS or key in {stem_entity_token(p) for p in PLACE_LEMMAS}:
        return "place"
    return "entity"


def is_noise_entity_surface(surface: str, *, author_keys: set[str]) -> bool:
    s = (surface or "").strip()
    if not s:
        return True
    if re.fullmatch(r"[IVXLCDM]+", s):
        return True
    n = norm_ru(s)
    if n in PLAY_STAGE_WORDS or n in EDITION_STOP:
        return True
    if n in _ENTITY_INTERJECTIONS:
        return True
    # «Какое-то», «кто-нибудь»
    compact = n.replace("-", "")
    if _PRONOUN_PARTICLE_RE.search(n) or compact.endswith(("нибудь", "либо")) and "-" in n:
        return True
    if n.endswith("-то") or n.endswith("то") and "-" in n:
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z\-]{2,}", s) and (
        s in author_keys or s.capitalize() in author_keys
    ):
        return True
    if n in qi.AUTHOR_ALIASES:
        return True
    return False


def drop_by_sentence_position(
    token: str,
    *,
    pos_stats: dict[str, dict[str, int]],
    case_stats: dict[str, dict[str, int]],
) -> bool:
    """Drop if almost always sentence-initial or mostly lowercase in corpus."""
    n = norm_ru(token)
    ps = pos_stats.get(n) or {}
    start_n = int(ps.get("sent_start", 0))
    mid_n = int(ps.get("mid", 0))
    total_pos = start_n + mid_n
    if total_pos >= 5 and (mid_n / total_pos < 0.30 or start_n >= mid_n * 2):
        return True
    st = case_stats.get(n) or {}
    low, up = int(st.get("lower", 0)), int(st.get("upper", 0))
    if low + up >= 6 and low > up * 2:
        return True
    if total_pos >= 3 and start_n >= 3 and mid_n == 0 and len(n) <= 10:
        return True
    return False


def entity_strength(
    surface: str,
    key: str,
    *,
    work_df: Counter[str],
    n_works: int,
    kind: str,
) -> str:
    """Return strong | weak | drop for a grouped entity."""
    n = norm_ru(surface)
    parts = n.split()
    df = int(work_df.get(key, 0))
    if key in _ENTITY_INTERJECTIONS or n in _ENTITY_INTERJECTIONS:
        return "drop"
    if kind == "religious" or key in {"бог", "господь", "христос", "евангелие"}:
        return "weak"
    max_df = max(2, int(0.15 * n_works))
    if df > max_df:
        return "drop"
    if kind == "place":
        return "strong" if df <= 3 else "weak"
    if len(parts) >= 2:
        # multiword name / ФИО
        return "strong"
    # single token — check surface (before aggressive lemma stem) for surname endings
    last = parts[-1] if parts else n
    if df <= 3 and (_SURNAME_RE.search(last) or _SURNAME_RE.search(key)):
        return "strong"
    if df <= 1 and len(key) >= 4:
        return "strong"  # unique proper
    # lone given name without patronymic/surname
    return "weak"


def layer_c_shared_entity(
    *,
    docs: list[DocMeta],
    doc_entities: dict[str, list[str]],
    doc_entity_chunks: dict[str, dict[str, list[str]]] | None = None,
    case_stats: dict[str, dict[str, int]] | None = None,
    pos_stats: dict[str, dict[str, int]] | None = None,
    params: GraphIndexParams | None = None,
) -> list[Edge]:
    """Shared entities grouped by lemma; require ≥1 strong; weight by groups."""
    params = params or GraphIndexParams()
    doc_entity_chunks = doc_entity_chunks or {}
    case_stats = case_stats or {}
    pos_stats = pos_stats or {}
    by_id = {d.document_id: d for d in docs}
    n_works = max(1, len({d.work_key for d in docs}))
    author_keys = {d.author_key for d in docs if d.author_key}

    work_entities: dict[str, set[str]] = defaultdict(set)
    doc_keys: dict[str, set[str]] = {}
    display: dict[str, str] = {}
    display_count: dict[str, Counter[str]] = defaultdict(Counter)
    kinds: dict[str, str] = {}

    for doc_id, ents in doc_entities.items():
        keys: set[str] = set()
        meta = by_id.get(doc_id)
        for e in ents[: params.entity_top_n * 2]:
            cleaned = clean_entity_surface(e)
            if not cleaned:
                continue
            if is_noise_entity_surface(cleaned, author_keys=author_keys):
                continue
            first = cleaned.split()[0]
            if drop_by_sentence_position(first, pos_stats=pos_stats, case_stats=case_stats):
                continue
            k = _entity_key(cleaned)
            if len(k) < 3 or k in EDITION_STOP or k in PLAY_STAGE_WORDS:
                continue
            if k in _ENTITY_INTERJECTIONS:
                continue
            keys.add(k)
            display_count[k][cleaned] += 1
            kinds[k] = entity_kind(cleaned, k)
        doc_keys[doc_id] = keys
        if meta:
            work_entities[meta.work_key] |= keys

    for k, ctr in display_count.items():
        display[k] = ctr.most_common(1)[0][0]

    work_df: Counter[str] = Counter()
    for ents in work_entities.values():
        for e in ents:
            work_df[e] += 1

    strength: dict[str, str] = {}
    for k in list(work_df.keys()):
        strength[k] = entity_strength(
            display.get(k, k),
            k,
            work_df=work_df,
            n_works=n_works,
            kind=kinds.get(k, "entity"),
        )

    # drop-strength entities out
    valid = {e for e, c in work_df.items() if c >= 2 and strength.get(e) != "drop"}
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
        base = math.log((n_works + 1) / (work_df[ent] + 1)) + 1.0
        st = strength.get(ent, "weak")
        if st == "weak" or kinds.get(ent) in {"place", "religious"}:
            return base * 0.3
        return base

    neigh: dict[str, list[tuple[float, str, set[str]]]] = defaultdict(list)
    for (a, b), common in pair_common.items():
        strong_common = {e for e in common if strength.get(e) == "strong"}
        if not strong_common:
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
            # dedupe display labels
            labels: list[str] = []
            seen_labels: set[str] = set()
            for e in sorted(common):
                lab = display.get(e, e)
                nl = norm_ru(lab)
                if nl in seen_labels:
                    continue
                seen_labels.add(nl)
                labels.append(lab)
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
                        "entities": labels[:10],
                        "entity_kinds": {
                            display.get(e, e): kinds.get(e, "entity") for e in sorted(common)[:10]
                        },
                        "entity_strength": {
                            display.get(e, e): strength.get(e, "weak") for e in sorted(common)[:10]
                        },
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


def is_name_surface_form(
    form: str,
    *,
    case_stats: dict[str, dict[str, int]],
    pos_stats: dict[str, dict[str, int]],
) -> bool:
    """Proper name if ≥70% of occurrences are capitalized mid-sentence/replica."""
    n = norm_ru(form)
    st = case_stats.get(n) or {}
    ps = pos_stats.get(n) or {}
    mid = int(ps.get("mid", 0))
    start = int(ps.get("sent_start", 0))
    low = int(st.get("lower", 0))
    total = mid + start + low
    if total < 2:
        # sparse: if only capitalized mid evidence
        up = int(st.get("upper", 0))
        return up >= 1 and low == 0 and mid >= start
    return (mid / total) >= 0.7


def collect_lex_name_exclusions(
    case_stats: dict[str, dict[str, int]] | None,
    pos_stats: dict[str, dict[str, int]] | None = None,
) -> tuple[set[str], set[str]]:
    """Return (exact_stems, prefix_bases≥4) to drop from lex terms."""
    case_stats = case_stats or {}
    pos_stats = pos_stats or {}
    name_forms = [
        f for f in case_stats if is_name_surface_form(f, case_stats=case_stats, pos_stats=pos_stats)
    ]
    name_stems = {stem_ru(f) for f in name_forms}
    name_stems |= {stem_ru(d) for d in _DIMINUTIVE_NAMES}
    name_stems |= set(_DIMINUTIVE_NAMES)
    # diminutive suffixes off name bases
    for form in list(name_forms):
        n = norm_ru(form)
        for suf in _DIMINUTIVE_SUFFIXES:
            if n.endswith(suf) and len(n) - len(suf) >= 3:
                name_stems.add(stem_ru(n[: -len(suf)]))
                name_stems.add(n)
    bases = {s[:4] for s in name_stems if len(s) >= 4}
    # expand: any name stem sharing base
    for s in list(name_stems):
        if len(s) >= 4:
            bases.add(s[:4])
    return name_stems, bases


def is_lex_proper_token(
    stem: str,
    case_stats: dict[str, dict[str, int]] | None,
    *,
    name_stems: set[str] | None = None,
    name_bases: set[str] | None = None,
    pos_stats: dict[str, dict[str, int]] | None = None,
) -> bool:
    """True for name-like / editorial / speech tokens to exclude from lex."""
    n = norm_ru(stem)
    if not n or len(n) < 4:
        return True
    if re.search(r"[a-z]", n):
        return True
    if n in _DIMINUTIVE_NAMES or n in qi.AUTHOR_ALIASES:
        return True
    if n in _EDITORIAL_STOP or stem_ru(n) in _EDITORIAL_STOP:
        return True
    if n in _SPEECH_VERBS or stem_ru(n) in _SPEECH_VERBS:
        return True
    if _PATRONYMIC_RE.search(n) or n.endswith(("ович", "евич", "овн", "евн", "ичн", "инич")):
        return True
    ns = name_stems or set()
    nb = name_bases or set()
    if n in ns or stem_ru(n) in ns:
        return True
    if len(n) >= 4 and n[:4] in nb:
        return True
    # fallback case bias on unstemmed keys
    if is_name_surface_form(n, case_stats=case_stats or {}, pos_stats=pos_stats or {}):
        return True
    st = (case_stats or {}).get(n) or {}
    up = int(st.get("upper", 0))
    low = int(st.get("lower", 0))
    if up + low >= 4 and up >= low * 2:
        return True
    return False


def layer_d_lex_similar(
    *,
    docs: list[DocMeta],
    doc_term_counts: dict[str, Counter[str]],
    params: GraphIndexParams | None = None,
    counter: LexCandidateCounter | None = None,
    case_stats: dict[str, dict[str, int]] | None = None,
    pos_stats: dict[str, dict[str, int]] | None = None,
) -> tuple[list[Edge], dict[str, Any]]:
    """Lex similarity on content words (names excluded before stemming)."""
    params = params or GraphIndexParams()
    case_stats = case_stats or {}
    pos_stats = pos_stats or {}
    by_id = {d.document_id: d for d in docs}
    n_docs = len(doc_term_counts)
    if n_docs < 2:
        return [], {"scores": [], "bugfix": "n_docs<2"}

    name_stems, name_bases = collect_lex_name_exclusions(case_stats, pos_stats)

    cleaned_counts: dict[str, Counter[str]] = {}
    for doc_id, counts in doc_term_counts.items():
        c2: Counter[str] = Counter()
        for t, c in counts.items():
            if is_lex_proper_token(
                t, case_stats, name_stems=name_stems, name_bases=name_bases, pos_stats=pos_stats
            ):
                continue
            c2[t] = c
        cleaned_counts[doc_id] = c2

    work_terms: dict[str, set[str]] = defaultdict(set)
    for doc_id, counts in cleaned_counts.items():
        meta = by_id.get(doc_id)
        wk = meta.work_key if meta else doc_id
        work_terms[wk] |= set(counts.keys())
    n_works = max(1, len(work_terms))
    df_work: Counter[str] = Counter()
    for terms in work_terms.values():
        for t in terms:
            df_work[t] += 1

    max_df = max(2, int(0.30 * n_works))
    vectors: dict[str, dict[str, float]] = {}
    inv: dict[str, list[str]] = defaultdict(list)
    for doc_id, counts in cleaned_counts.items():
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
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors[doc_id] = {t: v / norm for t, v in vec.items()}
        for t in vectors[doc_id]:
            inv[t].append(doc_id)

    pair_best: dict[tuple[str, str], tuple[float, list[str]]] = {}
    for t, members in inv.items():
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
                if len(shared) < 3:
                    continue
                contrib = {x: va[x] * vb[x] for x in shared}
                score = sum(contrib.values())
                if score <= 0:
                    continue
                if max(contrib.values()) > 0.5 * score:
                    continue
                key = (a, b) if a < b else (b, a)
                prev = pair_best.get(key)
                if not prev or score > prev[0]:
                    pair_best[key] = (score, sorted(shared)[:8])

    scores = sorted(s for s, _ in pair_best.values())
    thresh = params.lex_min_score
    if scores:
        p40 = scores[max(0, int(0.40 * (len(scores) - 1)))]
        if p40 > thresh:
            thresh = min(0.12, max(thresh, round(p40, 3)))

    neigh: dict[str, list[tuple[float, str, list[str]]]] = defaultdict(list)
    for (a, b), (score, terms) in pair_best.items():
        if score < thresh:
            continue
        neigh[a].append((score, b, terms))
        neigh[b].append((score, a, terms))

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    near_thresh: list[dict[str, Any]] = []
    for (a, b), (score, terms) in sorted(pair_best.items(), key=lambda x: abs(x[1][0] - thresh)):
        if len(near_thresh) >= 10:
            break
        near_thresh.append({"a": a, "b": b, "score": round(score, 4), "terms": terms})

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
                    provenance={"terms": terms, "shared_n": len(terms)},
                )
            )

    hist = Counter()
    for s in scores:
        bucket = round(s, 2)
        hist[bucket] += 1
    debug = {
        "bugfix": (
            "Names detected on surface forms before stemming (≥0.7 mid-cap); "
            "diminutives/editorial/speech dropped; ≥3 shared terms; "
            "single-term >50% score rejected."
        ),
        "score_count": len(scores),
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "score_hist": dict(sorted(hist.items())[:40]),
        "edges": len(edges),
        "threshold": thresh,
        "threshold_requested": params.lex_min_score,
        "near_threshold": near_thresh,
        "comparisons": counter.comparisons if counter else None,
        "name_stems_sample": sorted(name_stems)[:30],
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
    content_types = {
        "refers_to",
        "shared_entity",
        "lex_similar",
        "co_cited",
        "co_cited_eval",
        "mentions_author",
    }
    structural_types = {"wrote", "in_theme", "part_of", "work_meta"}
    content_linked: set[str] = set()
    structural_linked: set[str] = set()
    for e in edges:
        if e.type in content_types:
            content_linked.add(e.source)
            if not str(e.target).startswith("author:"):
                content_linked.add(e.target)
        if e.type in structural_types or e.type in content_types or e.type.startswith("work_"):
            if not str(e.source).startswith("author:"):
                structural_linked.add(e.source.removeprefix("work:"))
            if not str(e.target).startswith("author:"):
                structural_linked.add(e.target.removeprefix("work:"))
            # also mark doc endpoints
            if e.type in structural_types | content_types:
                structural_linked.add(e.source)
                if not str(e.target).startswith("author:"):
                    structural_linked.add(e.target)

    by_id = {d.document_id: d for d in docs}
    isolated_content_ids = [d.document_id for d in docs if d.document_id not in content_linked]
    isolated_structural_ids = [
        d.document_id
        for d in docs
        if d.document_id not in structural_linked and d.work_key not in structural_linked
    ]

    def _iso_reason(d: DocMeta) -> str:
        if d.chunk_count <= 0:
            return "empty_after_clean"
        # rough: no clean text signal if no chunks
        if not d.author_key:
            return "no_author"
        if d.chunk_count <= 2:
            return "short_text"
        return "no_content_edges"

    isolated_content_top = sorted(
        [
            {
                "document_id": did,
                "title": by_id[did].title,
                "author_key": by_id[did].author_key,
                "chunk_count": by_id[did].chunk_count,
                "reason": _iso_reason(by_id[did]),
            }
            for did in isolated_content_ids
            if did in by_id
        ],
        key=lambda r: (-int(r["chunk_count"] or 0), r["document_id"]),
    )[:20]

    works = {d.work_key: d for d in docs}
    work_linked: set[str] = set()
    for e in edges:
        if e.type.startswith("work_") and e.type != "work_meta":
            work_linked.add(e.source.removeprefix("work:"))
            if not str(e.target).startswith("author:"):
                work_linked.add(e.target.removeprefix("work:"))
        if e.type in ("refers_to", "shared_entity", "lex_similar", "co_cited", "co_cited_eval"):
            sa = by_id.get(e.source)
            sb = by_id.get(e.target)
            if sa:
                work_linked.add(sa.work_key)
            if sb:
                work_linked.add(sb.work_key)
    isolated_works = [wk for wk in works if wk not in work_linked]
    no_author = [d for d in docs if not d.author_key]
    frag_docs = [d.document_id for d in docs if d.fragment_no is not None]
    work_frag: Counter[str] = Counter(d.work_key for d in docs)
    dup_count = sum(1 for d in docs if not d.canonical)

    # Degree rankings: works vs authors separately
    work_degree: Counter[str] = Counter()
    author_degree: Counter[str] = Counter()
    for e in edges:
        for node in (e.source, e.target):
            s = str(node)
            if s.startswith("author:"):
                author_degree[s] += 1
            elif s.startswith("work:"):
                work_degree[s] += 1

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
        "isolated_documents": len(isolated_content_ids),  # back-compat alias
        "isolated_content": len(isolated_content_ids),
        "isolated_structural": len(isolated_structural_ids),
        "isolated_content_top20": isolated_content_top,
        "isolated_document_ids_sample": isolated_content_ids[:20],
        "isolated_works": len(isolated_works),
        "isolated_work_share": round(len(isolated_works) / max(1, len(works)), 4),
        "fragment_documents": len(frag_docs),
        "documents_without_author": len(no_author),
        "documents_without_author_sample": [d.document_id for d in no_author[:20]],
        "documents_without_author_preview": [
            {
                "document_id": d.document_id,
                "title": d.title,
                "filename": d.filename,
            }
            for d in no_author[:40]
        ],
        "author_source_counts": dict(Counter(d.author_source or "none" for d in docs)),
        "duplicate_documents": dup_count,
        "canonical_documents": sum(1 for d in docs if d.canonical),
        "top_works_by_fragments": [
            {
                "work_key": wk,
                "fragment_count": n,
                "title": works[wk].title,
                "author_key": works[wk].author_key,
            }
            for wk, n in work_frag.most_common(20)
        ],
        "top_works_by_degree": [
            {"node": wk, "degree": n}
            for wk, n in work_degree.most_common(20)
        ],
        "top_authors_by_degree": [
            {"node": ak, "degree": n}
            for ak, n in author_degree.most_common(20)
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
                "author_source": d.author_source,
                "title": d.title,
                "fragment_no": d.fragment_no,
                "part_no": d.part_no,
                "volume_no": d.volume_no,
                "work_key": d.work_key,
                "chunk_count": d.chunk_count,
                "created_at": d.created_at,
                "canonical": d.canonical,
                "duplicate_of": d.duplicate_of,
            }
            for d in docs
        ],
        "edges": [e.to_dict() for e in edges],
        "stats": stats,
    }



def graph_dir() -> Path:
    return gcache.graph_dir()


def index_path(
    tenant_id: UUID | str,
    fingerprint: str,
    *,
    params: GraphIndexParams | None = None,
    params_hash_value: str | None = None,
) -> Path:
    ph = params_hash_value or gcache.params_hash((params or GraphIndexParams()).to_dict())
    return gcache.index_path(
        tenant_id,
        fingerprint,
        index_version=INDEX_VERSION,
        params_hash_value=ph,
    )


def purge_old_indexes(
    tenant_id: UUID | str,
    keep_fingerprint: str | Path,
    *,
    params: GraphIndexParams | None = None,
    keep_n: int = gcache.KEEP_INDEX_FILES,
) -> int:
    """Remove stale index files; keep current path (and last N)."""
    if isinstance(keep_fingerprint, Path):
        keep = keep_fingerprint
    else:
        keep = index_path(tenant_id, str(keep_fingerprint), params=params)
    return gcache.purge_old_indexes(tenant_id, keep, keep_n=keep_n)


def invalidate_tenant(tenant_id: UUID | str) -> dict[str, int]:
    """Drop indexes + doc artifacts after reset-rag / corpus clear."""
    return gcache.invalidate_tenant_graph(tenant_id)


def write_index_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Atomic index write: tmp + rename. Failed builds must not call this."""
    gcache.write_json_atomic(path, payload, indent=2)


def read_index(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _annotate_stale(cached: dict[str, Any], tenant_id: UUID | str) -> dict[str, Any]:
    ptr = gcache.read_current_pointer(tenant_id) or {}
    stale = bool(ptr.get("stale"))
    stats = dict(cached.get("stats") or {})
    stats["stale"] = stale
    if stale:
        stats["stale_reason"] = ptr.get("stale_reason")
    cached = dict(cached)
    cached["stats"] = stats
    cached["stale"] = stale
    return cached


def load_cached_index(db: Session, tenant_id: UUID, *, params: GraphIndexParams | None = None) -> dict[str, Any] | None:
    """Return exact cache hit, or last-good pointer if rebuild failed / corpus pending.

    Failed builds never overwrite the pointer, so API keeps serving the previous intact index
    (with stats.stale=true when corpus changed).
    """
    params = params or GraphIndexParams()
    fp = corpus_fingerprint(db, tenant_id)
    ph = gcache.params_hash(params.to_dict())
    exact = read_index(index_path(tenant_id, fp, params_hash_value=ph))
    if (
        exact
        and exact.get("fingerprint") == fp
        and exact.get("version") == INDEX_VERSION
        and exact.get("params_hash") in (None, ph)
    ):
        return _annotate_stale(exact, tenant_id)

    ptr = gcache.read_current_pointer(tenant_id) or {}
    prev_name = ptr.get("index_file")
    if prev_name:
        prev = read_index(gcache.graph_dir() / str(prev_name))
        if prev and prev.get("version") == INDEX_VERSION:
            return _annotate_stale(prev, tenant_id)
    return None


def on_corpus_changed(tenant_id: UUID, *, reason: str = "corpus_changed") -> dict[str, Any]:
    """Mark connectivity index stale; optionally schedule rebuild (GRAPH_AUTO_REBUILD)."""
    ptr = gcache.mark_index_stale(tenant_id, reason=reason)
    from apps.api.services.graph.settings import get_graph_limits

    auto = bool(get_graph_limits().auto_rebuild)
    out: dict[str, Any] = {"stale": True, "reason": reason, "auto_rebuild": auto, "pointer": ptr}
    if auto:
        out["rebuild"] = schedule_rebuild(tenant_id, force=True, mode="auto")
    return out


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


def _chunks_for_version(
    db: Session, tenant_id: UUID, version_id: UUID
) -> list[tuple[str, int, str]]:
    rows = (
        db.query(Chunk.id, Chunk.ordinal, Chunk.text)
        .filter(Chunk.tenant_id == tenant_id, Chunk.version_id == version_id)
        .order_by(Chunk.ordinal)
        .all()
    )
    return [(str(cid), int(ord_n or 0), text or "") for cid, ord_n, text in rows]


def _apply_header_author(meta: DocMeta, hdr: dict[str, Any]) -> None:
    if meta.author_key or not hdr.get("author"):
        return
    ak = str(hdr["author"]).strip()
    if not ak or norm_ru(ak) in LANGUAGE_MARKERS:
        return
    mapped = None
    for ru, key in qi.AUTHOR_ALIASES.items():
        if norm_ru(ak) == ru or ak == key:
            mapped = key
            break
    meta.author_key = mapped or (ak if re.fullmatch(r"[A-Z][A-Za-z\-]+", ak) else None)
    if meta.author_key:
        meta.author_ru = _AUTHOR_RU_BY_KEY.get(meta.author_key)
        meta.author_source = "ingest_header"
        meta.work_key = work_key_for(pack=meta.pack, author_key=meta.author_key, title=meta.title)


def _build_doc_artifact(
    *,
    meta: DocMeta,
    version_id: str,
    raw_chunks: list[tuple[str, int, str]],
    params: GraphIndexParams,
) -> dict[str, Any]:
    """Process one document into a reusable offline artifact."""
    clean_chunks: list[dict[str, Any]] = []
    entities: list[str] = []
    entity_chunks: dict[str, list[str]] = defaultdict(list)
    term_counts: Counter[str] = Counter()
    case_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"upper": 0, "lower": 0})
    pos_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"sent_start": 0, "mid": 0})
    chars_removed = 0
    examples: list[dict[str, str]] = []
    raw_texts = [t for _, _, t in raw_chunks]
    chash = gcache.content_hash(
        version_id=version_id, filename=meta.filename, chunk_texts=raw_texts
    )
    # A5: author from first-chunk text header (before clean), if still unknown
    if not meta.author_key and raw_chunks:
        first_raw = sorted(raw_chunks, key=lambda x: x[1])[0][2]
        ak = gauth.detect_author_from_text_header(first_raw or "")
        if ak:
            meta.author_key = ak
            meta.author_ru = _AUTHOR_RU_BY_KEY.get(ak)
            meta.author_source = "text_header"
            meta.work_key = work_key_for(pack=meta.pack, author_key=meta.author_key, title=meta.title)

    for chunk_id, ord_n, text in raw_chunks:
        clean, hdr = clean_chunk_for_graph(
            text,
            chunk_ordinal=ord_n,
            doc_meta={
                "author_key": meta.author_key,
                "author_ru": meta.author_ru,
                "title": meta.title,
            },
        )
        _apply_header_author(meta, hdr)
        removed = int(hdr.get("chars_removed") or 0)
        chars_removed += removed
        if removed > 0 and len(examples) < 2:
            examples.append({"before": (text or "")[:220], "after": clean[:220]})
        clean_chunks.append({"id": chunk_id, "ordinal": ord_n, "text": clean})
        for m in re.finditer(r"[A-Za-zА-Яа-яЁё]{3,}", clean, flags=re.UNICODE):
            w = m.group(0)
            key = norm_ru(w)
            if w[0].isupper():
                case_stats[key]["upper"] += 1
                if _is_sentence_start(clean, m.start()):
                    pos_stats[key]["sent_start"] += 1
                else:
                    pos_stats[key]["mid"] += 1
            else:
                case_stats[key]["lower"] += 1
        for e in extract_entity_candidates([clean], limit=params.entity_top_n):
            cleaned = clean_entity_surface(e) or e
            k = _entity_key(cleaned)
            if cleaned not in entities:
                entities.append(cleaned)
            if chunk_id not in entity_chunks[k]:
                entity_chunks[k].append(chunk_id)
        for tok in tokenize_lex(clean):
            term_counts[tok] += 1
    return {
        "document_id": meta.document_id,
        "version_id": version_id,
        "filename": meta.filename,
        "content_hash": chash,
        "author_key": meta.author_key,
        "author_ru": meta.author_ru,
        "author_source": meta.author_source,
        "title": meta.title,
        "work_key": meta.work_key,
        "clean_chunks": clean_chunks,
        "entities": entities,
        "entity_chunks": dict(entity_chunks),
        "term_counts": dict(term_counts),
        "case_stats": {k: dict(v) for k, v in case_stats.items()},
        "pos_stats": {k: dict(v) for k, v in pos_stats.items()},
        "chars_removed": chars_removed,
        "examples": examples,
    }


def _empty_index_payload(
    *,
    tenant_id: str,
    fingerprint: str,
    params: GraphIndexParams,
    built_ms: int,
    mode: str,
    params_hash_value: str,
) -> dict[str, Any]:
    payload = assemble_index(
        tenant_id=tenant_id,
        fingerprint=fingerprint,
        docs=[],
        edges=[],
        params=params,
        built_ms=built_ms,
        extra_stats={
            "build_mode": mode,
            "lifecycle": {
                "added": 0,
                "changed": 0,
                "removed": 0,
                "cached": 0,
            },
            "layer_ms": {},
            "params_hash": params_hash_value,
            "text_clean": {"chars_removed": 0, "chunks_touched": 0, "examples": []},
        },
    )
    payload["params_hash"] = params_hash_value
    return payload


def build_index_for_tenant(
    db: Session,
    tenant_id: UUID,
    *,
    params: GraphIndexParams | None = None,
    force: bool = False,
    mode: str | None = None,
) -> dict[str, Any]:
    """Build connectivity index. ``mode``: full | incremental | None(auto).

    Per-doc artifacts are reused when content_hash matches (incremental).
    Global layers always recompute from the artifact set.
    """
    params = params or GraphIndexParams()
    t0 = time.perf_counter()
    fp = corpus_fingerprint(db, tenant_id)
    ph = gcache.params_hash(params.to_dict())
    path = index_path(tenant_id, fp, params_hash_value=ph)
    if not force:
        cached = read_index(path)
        if (
            cached
            and cached.get("fingerprint") == fp
            and cached.get("version") == INDEX_VERSION
            and cached.get("params_hash") in (None, ph)
        ):
            log.info("graph_index_cache_hit", tenant_id=str(tenant_id), fingerprint=fp)
            return cached

    rows = _active_documents(db, tenant_id)
    if mode in ("full", "incremental"):
        build_mode = mode
    else:
        # auto: reuse per-doc caches when present; otherwise first full scan
        build_mode = (
            "incremental" if any(gcache.doc_cache_dir(tenant_id).glob("*.json")) else "full"
        )

    if not rows:
        payload = _empty_index_payload(
            tenant_id=str(tenant_id),
            fingerprint=fp,
            params=params,
            built_ms=int((time.perf_counter() - t0) * 1000),
            mode=build_mode,
            params_hash_value=ph,
        )
        return _publish_index(
            tenant_id=tenant_id,
            path=path,
            payload=payload,
            fingerprint=fp,
            params_hash_value=ph,
            build_mode=build_mode,
            lifecycle={"added": 0, "changed": 0, "removed": 0, "cached": 0},
            layer_ms={},
            chunk_total=0,
        )

    docs: list[DocMeta] = []
    ver_by_doc: dict[str, DocumentVersion] = {}
    for doc, ver in rows:
        created = doc.created_at.isoformat() if doc.created_at else None
        meta = parse_doc_meta(
            document_id=str(doc.id),
            filename=ver.original_name,
            chunk_count=int(ver.indexed_chunk_count or 0),
            created_at=created,
        )
        docs.append(meta)
        ver_by_doc[meta.document_id] = ver
    resolve_work_keys(docs)

    layer_ms: dict[str, int] = {}
    lifecycle = {"added": 0, "changed": 0, "removed": 0, "cached": 0}
    t_scan = time.perf_counter()
    artifacts: dict[str, dict[str, Any]] = {}
    keep_ids = {d.document_id for d in docs}
    lifecycle["removed"] = gcache.purge_doc_artifacts(tenant_id, keep_ids)

    for meta in docs:
        ver = ver_by_doc[meta.document_id]
        raw_chunks = _chunks_for_version(db, tenant_id, ver.id)
        chash = gcache.content_hash(
            version_id=str(ver.id),
            filename=meta.filename,
            chunk_texts=[t for _, _, t in raw_chunks],
        )
        # Reuse per-doc artifacts whenever content_hash matches (full rebuild still
        # recomputes global stats/edges; only changed docs are re-scanned).
        cached_art = gcache.read_doc_artifact(tenant_id, meta.document_id)
        if cached_art and cached_art.get("content_hash") == chash:
            artifacts[meta.document_id] = cached_art
            lifecycle["cached"] += 1
            if cached_art.get("author_key") and not meta.author_key:
                meta.author_key = cached_art.get("author_key")
                meta.author_ru = cached_art.get("author_ru")
                meta.author_source = cached_art.get("author_source") or "ingest_header"
                meta.work_key = work_key_for(
                    pack=meta.pack, author_key=meta.author_key, title=meta.title
                )
            continue
        art = _build_doc_artifact(
            meta=meta,
            version_id=str(ver.id),
            raw_chunks=raw_chunks,
            params=params,
        )
        gcache.write_doc_artifact(tenant_id, meta.document_id, art)
        artifacts[meta.document_id] = art
        if cached_art:
            lifecycle["changed"] += 1
        else:
            lifecycle["added"] += 1
    layer_ms["scan_clean"] = int((time.perf_counter() - t_scan) * 1000)
    resolve_work_keys(docs)

    # Aggregate artifact fields into layer inputs
    doc_entities: dict[str, list[str]] = {}
    doc_entity_chunks: dict[str, dict[str, list[str]]] = {}
    doc_term_counts: dict[str, Counter[str]] = {}
    case_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"upper": 0, "lower": 0})
    pos_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"sent_start": 0, "mid": 0})
    chunk_rows: list[tuple[str, str, str]] = []
    clean_chars_removed = 0
    clean_examples: list[dict[str, str]] = []
    chunk_total = 0
    for meta in docs:
        art = artifacts[meta.document_id]
        doc_entities[meta.document_id] = list(art.get("entities") or [])
        doc_entity_chunks[meta.document_id] = dict(art.get("entity_chunks") or {})
        doc_term_counts[meta.document_id] = Counter(art.get("term_counts") or {})
        for k, v in (art.get("case_stats") or {}).items():
            case_stats[k]["upper"] += int(v.get("upper") or 0)
            case_stats[k]["lower"] += int(v.get("lower") or 0)
        for k, v in (art.get("pos_stats") or {}).items():
            pos_stats[k]["sent_start"] += int(v.get("sent_start") or 0)
            pos_stats[k]["mid"] += int(v.get("mid") or 0)
        clean_chars_removed += int(art.get("chars_removed") or 0)
        for ex in art.get("examples") or []:
            if len(clean_examples) < 5:
                clean_examples.append({"doc_id": meta.document_id, **ex})
        for ch in art.get("clean_chunks") or []:
            chunk_rows.append((meta.document_id, str(ch.get("id")), ch.get("text") or ""))
            chunk_total += 1
        meta.chunk_count = len(art.get("clean_chunks") or [])

    # A6: near-duplicate editions within work_key (stdlib shingles)
    texts_by_id = {
        did: "\n".join(ch.get("text") or "" for ch in (art.get("clean_chunks") or []))
        for did, art in artifacts.items()
    }
    dup_map = gdup.mark_canonical_duplicates(docs, texts_by_id)
    canon_ids = gdup.canonical_doc_ids(dup_map)
    for meta in docs:
        info = dup_map.get(meta.document_id) or {}
        meta.canonical = bool(info.get("canonical", True))
        meta.duplicate_of = info.get("duplicate_of")

    # Rebuild global stats from canonical docs only
    case_stats = defaultdict(lambda: {"upper": 0, "lower": 0})
    pos_stats = defaultdict(lambda: {"sent_start": 0, "mid": 0})
    for meta in docs:
        if meta.document_id not in canon_ids:
            continue
        art = artifacts[meta.document_id]
        for k, v in (art.get("case_stats") or {}).items():
            case_stats[k]["upper"] += int(v.get("upper") or 0)
            case_stats[k]["lower"] += int(v.get("lower") or 0)
        for k, v in (art.get("pos_stats") or {}).items():
            pos_stats[k]["sent_start"] += int(v.get("sent_start") or 0)
            pos_stats[k]["mid"] += int(v.get("mid") or 0)

    # Textual layers use canonical docs only (duplicates fold at work level)
    docs_canon = [d for d in docs if d.document_id in canon_ids]
    chunk_rows_canon = [r for r in chunk_rows if r[0] in canon_ids]
    doc_entities_canon = {k: v for k, v in doc_entities.items() if k in canon_ids}
    doc_entity_chunks_canon = {k: v for k, v in doc_entity_chunks.items() if k in canon_ids}
    doc_term_counts_canon = {k: v for k, v in doc_term_counts.items() if k in canon_ids}

    edges: list[Edge] = []
    t_l = time.perf_counter()
    edges.extend(layer_a_edges(docs))
    layer_ms["layer_a"] = int((time.perf_counter() - t_l) * 1000)

    t_l = time.perf_counter()
    edges.extend(layer_mentions_author(docs=docs_canon, chunk_iter=chunk_rows_canon))
    layer_ms["mentions_author"] = int((time.perf_counter() - t_l) * 1000)

    t_l = time.perf_counter()
    edges.extend(layer_b_refers_to(docs=docs_canon, chunk_iter=chunk_rows_canon, params=params))
    layer_ms["refers_to"] = int((time.perf_counter() - t_l) * 1000)

    t_l = time.perf_counter()
    edges.extend(
        layer_c_shared_entity(
            docs=docs_canon,
            doc_entities=doc_entities_canon,
            doc_entity_chunks=doc_entity_chunks_canon,
            case_stats={k: dict(v) for k, v in case_stats.items()},
            pos_stats={k: dict(v) for k, v in pos_stats.items()},
            params=params,
        )
    )
    layer_ms["shared_entity"] = int((time.perf_counter() - t_l) * 1000)

    t_l = time.perf_counter()
    lex_edges, lex_debug = layer_d_lex_similar(
        docs=docs_canon,
        doc_term_counts=doc_term_counts_canon,
        params=params,
        counter=LexCandidateCounter(),
        case_stats={k: dict(v) for k, v in case_stats.items()},
        pos_stats={k: dict(v) for k, v in pos_stats.items()},
    )
    edges.extend(lex_edges)
    layer_ms["lex_similar"] = int((time.perf_counter() - t_l) * 1000)

    t_l = time.perf_counter()
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
    layer_ms["co_cited_and_work_agg"] = int((time.perf_counter() - t_l) * 1000)

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
            "duplicates": {
                "canonical": sum(1 for d in docs if d.canonical),
                "duplicate": sum(1 for d in docs if not d.canonical),
                "pairs": [
                    {"doc": d.document_id, "duplicate_of": d.duplicate_of, "sim": (dup_map.get(d.document_id) or {}).get("sim")}
                    for d in docs
                    if not d.canonical
                ][:40],
            },
            "documents_without_author_preview": [
                {
                    "document_id": d.document_id,
                    "title": d.title,
                    "filename": d.filename,
                    "head": (texts_by_id.get(d.document_id) or "")[:200],
                }
                for d in docs
                if not d.author_key
            ][:40],
            "source_tokens": sorted(SOURCE_TOKENS),
            "text_clean": {
                "chars_removed": clean_chars_removed,
                "chunks_touched": sum(1 for a in artifacts.values() if int(a.get("chars_removed") or 0) > 0),
                "examples": clean_examples,
            },
            "layer_ms": layer_ms,
            "build_mode": build_mode,
            "lifecycle": lifecycle,
            "params_hash": ph,
        },
    )
    payload["params_hash"] = ph
    return _publish_index(
        tenant_id=tenant_id,
        path=path,
        payload=payload,
        fingerprint=fp,
        params_hash_value=ph,
        build_mode=build_mode,
        lifecycle=lifecycle,
        layer_ms=layer_ms,
        chunk_total=chunk_total,
    )


def _publish_index(
    *,
    tenant_id: UUID | str,
    path: Path,
    payload: dict[str, Any],
    fingerprint: str,
    params_hash_value: str,
    build_mode: str,
    lifecycle: dict[str, Any],
    layer_ms: dict[str, Any],
    chunk_total: int,
) -> dict[str, Any]:
    """Atomic publish of a complete index; never called mid-build."""
    write_index_atomic(path, payload)
    gcache.write_current_pointer(
        tenant_id,
        {
            "index_file": path.name,
            "fingerprint": fingerprint,
            "params_hash": params_hash_value,
            "version": INDEX_VERSION,
            "stale": False,
            "built_at": payload.get("built_at"),
        },
    )
    gcache.clear_stale_flag(tenant_id)
    removed = purge_old_indexes(tenant_id, path)
    stats = payload.setdefault("stats", {})
    stats["stale"] = False
    gcache.write_build_status(
        tenant_id,
        {
            "status": "ready",
            "mode": build_mode,
            "fingerprint": fingerprint,
            "params_hash": params_hash_value,
            "built_ms": payload.get("built_ms"),
            "documents": stats.get("documents"),
            "lifecycle": lifecycle,
            "layer_ms": layer_ms,
            "stale": False,
            "index_file": path.name,
        },
    )
    log.info(
        "graph_index_built",
        tenant_id=str(tenant_id),
        fingerprint=fingerprint,
        built_ms=payload.get("built_ms"),
        mode=build_mode,
        documents=stats.get("documents"),
        works=stats.get("works"),
        chunks=chunk_total,
        edge_counts=stats.get("edge_counts_by_type"),
        isolated_docs=stats.get("isolated_documents"),
        isolated_works=stats.get("isolated_works"),
        purged_old=removed,
        lifecycle=lifecycle,
    )
    return payload


def schedule_rebuild(
    tenant_id: UUID,
    *,
    force: bool = True,
    mode: str | None = None,
) -> dict[str, Any]:
    """Enqueue background rebuild; one build per tenant via file lock + in-memory flag."""
    tid = str(tenant_id)
    mem = gcache.tenant_lock(tid)
    if not mem.acquire(blocking=False):
        st = gcache.read_build_status(tid) or {}
        return {"status": "already_running", "build": st}
    if not gcache.acquire_build_lock(tid):
        mem.release()
        st = gcache.read_build_status(tid) or {}
        return {"status": "already_running", "build": st}

    gcache.set_build_running(tid, True)
    gcache.write_build_status(
        tid,
        {
            "status": "running",
            "mode": mode or "auto",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "stale": True,
        },
    )

    def _worker() -> None:
        from apps.api.db import SessionLocal

        try:
            with SessionLocal() as db:
                build_index_for_tenant(db, tenant_id, force=force, mode=mode)
        except Exception as exc:  # noqa: BLE001
            log.exception("graph_index_rebuild_failed", tenant_id=tid, error=type(exc).__name__)
            # Keep previous current pointer / index intact.
            gcache.write_build_status(
                tid,
                {
                    "status": "error",
                    "error": type(exc).__name__,
                    "message": str(exc)[:300],
                    "stale": True,
                },
            )
            gcache.mark_index_stale(tid, reason=f"build_error:{type(exc).__name__}")
        finally:
            gcache.set_build_running(tid, False)
            gcache.release_build_lock(tid)
            try:
                mem.release()
            except RuntimeError:
                pass

    threading.Thread(target=_worker, daemon=True, name=f"graph-index-{tid[:8]}").start()
    return {"status": "started", "mode": mode or "auto"}


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
