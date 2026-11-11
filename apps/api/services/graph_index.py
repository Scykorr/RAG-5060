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
from apps.api.services.graph_lexicon import dict_field, frozenset_field, tuple_field
from apps.api.services.quality_state import corpus_fingerprint
from apps.api.services.storage import data_root
from apps.api.services.graph_text_clean import clean_chunk_for_graph
from apps.api.services import graph_refers as grefer
from apps.api.services import graph_authors as gauth
from apps.api.services import graph_duplicates as gdup
from apps.api.services import graph_overrides as govr
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

INDEX_VERSION = 10

# Filename grammar tokens (source-site / language folder markers), not corpus content.
SOURCE_TOKENS = frozenset({"ch", "eb"})
LANGUAGE_MARKERS = frozenset({"russian", "english", "deutsch", "french"})

DEFAULT_CHUNK_BATCH = 200
DEFAULT_ENTITY_TOP_N = 20
DEFAULT_SHARED_MIN = 2
DEFAULT_SHARED_TOP_K = 8
DEFAULT_LEX_TOP_M = 200
DEFAULT_LEX_TOP_K = 5
DEFAULT_LEX_MIN_SCORE = 0.08
DEFAULT_LEX_WEAK_FLOOR = 0.05
DEFAULT_LEX_BURST_MAX_RATIO = 0.90
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
_STYLE_BURST_HINTS = frozenset(
    stem_ru(v) for v in (tuple_field("style_burst_hints") or ())
) | frozenset(norm_ru(v) for v in (tuple_field("style_burst_hints") or ()))
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
    lex_weak_floor: float = DEFAULT_LEX_WEAK_FLOOR
    lex_burst_max_ratio: float = DEFAULT_LEX_BURST_MAX_RATIO
    refers_top_k: int = DEFAULT_REFERS_TOP_K
    refers_hub_frac: float = DEFAULT_REFERS_HUB_FRAC
    entity_max_work_frac: float = DEFAULT_ENTITY_MAX_WORK_FRAC
    refers_common_work_df: int = DEFAULT_REFERS_COMMON_WORK_DF
    work_neighbor_top_k: int = DEFAULT_WORK_NEIGHBOR_TOP_K
    include_eval_cocited: bool = False
    co_cited_max_sources: int = 6
    co_cited_weight_scale: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_batch": self.chunk_batch,
            "entity_top_n": self.entity_top_n,
            "shared_min_entities": self.shared_min_entities,
            "shared_top_k": self.shared_top_k,
            "lex_top_m": self.lex_top_m,
            "lex_top_k": self.lex_top_k,
            "lex_min_score": self.lex_min_score,
            "lex_weak_floor": self.lex_weak_floor,
            "lex_burst_max_ratio": self.lex_burst_max_ratio,
            "refers_top_k": self.refers_top_k,
            "refers_hub_frac": self.refers_hub_frac,
            "entity_max_work_frac": self.entity_max_work_frac,
            "refers_common_work_df": self.refers_common_work_df,
            "work_neighbor_top_k": self.work_neighbor_top_k,
            "include_eval_cocited": self.include_eval_cocited,
            "co_cited_max_sources": self.co_cited_max_sources,
            "co_cited_weight_scale": self.co_cited_weight_scale,
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
    author_source: str | None = None  # filename | ingest_header | text_header | work_merge | override
    canonical: bool = True
    duplicate_of: str | None = None
    contained_in: str | None = None
    relation: str = "canonical"  # canonical | duplicate | contained_in


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

    # Ambiguous titles: same norm used by ≥2 authors
    title_authors: dict[str, set[str]] = defaultdict(set)
    title_by_author: dict[tuple[str, str], str] = {}
    for phrase_n, did, _surf, _multi in phrases:
        meta = by_id.get(did)
        if meta and meta.author_key:
            title_authors[phrase_n].add(meta.author_key)
            title_by_author[(phrase_n, meta.author_key)] = did
    title_author_count = {k: len(v) for k, v in title_authors.items()}

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
            title_toks = grefer.title_tokens(surface)
            q_starters = grefer.question_title_starters()
            question_title = bool(title_toks and title_toks[0] in q_starters)
            person_name_title = (not multi) and bool(
                is_name_surface_form(surface, case_stats={}, pos_stats={})
                or (surface[:1].isupper() and len(title_toks) == 1 and _SURNAME_RE.search(norm_ru(surface)))
                or (surface[:1].isupper() and len(norm_ru(surface)) <= 8 and norm_ru(surface) in _DIMINUTIVE_NAMES)
            )
            # Prefer same-author target when title is ambiguous across authors
            ambiguous = title_author_count.get(phrase_n, 0) >= 2
            if ambiguous and src_meta and src_meta.author_key:
                own = title_by_author.get((phrase_n, src_meta.author_key))
                if own and own != target_id:
                    continue
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
                    person_name_title=person_name_title,
                    question_title=question_title,
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
            conf = "high"
            if grefer.foreign_marker_near(raw, best["start"], best["end"]):
                conf = "low"
                w *= 0.3
            if ambiguous and not same_author and best.get("match_form") != "quoted":
                # other author's title without target author nearby
                tokens = grefer._tokens_with_spans(raw)
                if not grefer._near_author_tokens(
                    tokens, best["start"], best["end"], tgt_meta.author_key if tgt_meta else None
                ):
                    conf = "low"
                    w *= 0.3
            bucket = hits[doc_id][target_id]
            if chunk_id not in bucket["chunks"]:
                bucket["chunks"].append(chunk_id)
                bucket["weight_sum"] += w
            if conf == "low":
                bucket["confidence"] = "low"
            elif not bucket.get("confidence"):
                bucket["confidence"] = conf
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
                        "confidence": info.get("confidence") or "high",
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
    # E4: strong only when rare across works (df ≤ 3) after form merge
    if df > 3:
        return "weak"
    if len(parts) >= 2:
        # multiword name / ФИО — still weak if common; strong only if rare
        return "strong"
    last = parts[-1] if parts else n
    if _SURNAME_RE.search(last) or _SURNAME_RE.search(key):
        return "strong"
    if df <= 1 and len(key) >= 4:
        return "strong"
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

    def _author_fio_keys(meta: DocMeta | None) -> set[str]:
        if not meta or not meta.author_key:
            return set()
        table = dict_field("author_name_table").get(meta.author_key) or {}
        out: set[str] = set()
        for bucket in ("first", "patronymic", "surnames"):
            for x in table.get(bucket) or []:
                out.add(_entity_key(str(x)))
        # common FIO compounds for this author
        firsts = [norm_ru(x) for x in (table.get("first") or [])]
        pats = [norm_ru(x) for x in (table.get("patronymic") or [])]
        for f in firsts:
            for p in pats:
                out.add(_entity_key(f"{f} {p}"))
        out.add(_entity_key(meta.author_key))
        if meta.author_ru:
            out.add(_entity_key(meta.author_ru))
        return {x for x in out if x}

    def _title_keys(meta: DocMeta | None) -> set[str]:
        if not meta:
            return set()
        out = {_entity_key(meta.title)}
        for part in re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", meta.title or ""):
            out.add(_entity_key(part))
        return {x for x in out if x}

    for doc_id, ents in doc_entities.items():
        keys: set[str] = set()
        meta = by_id.get(doc_id)
        skip_keys = _author_fio_keys(meta) | _title_keys(meta)
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
            if k in skip_keys:
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
        ma, mb = by_id.get(a), by_id.get(b)
        same_author = bool(
            ma and mb and ma.author_key and mb.author_key and ma.author_key == mb.author_key
        )
        # Cross-author: need ≥2 distinct strong entities
        if not same_author and len(strong_common) < 2:
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
    if n in PLAY_STAGE_WORDS or stem_ru(n) in PLAY_STAGE_WORDS:
        return True
    if n in _STYLE_BURST_HINTS or stem_ru(n) in _STYLE_BURST_HINTS:
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


def _expected_df(n_works: int, cf: int) -> float:
    """Expected df if ``cf`` occurrences placed independently into works."""
    if n_works <= 0 or cf <= 0:
        return 0.0
    # 1 - (1 - 1/N)^cf
    return float(n_works) * (1.0 - (1.0 - 1.0 / n_works) ** cf)


def layer_d_lex_similar(
    *,
    docs: list[DocMeta],
    doc_term_counts: dict[str, Counter[str]],
    params: GraphIndexParams | None = None,
    counter: LexCandidateCounter | None = None,
    case_stats: dict[str, dict[str, int]] | None = None,
    pos_stats: dict[str, dict[str, int]] | None = None,
) -> tuple[list[Edge], dict[str, Any]]:
    """Work-level lex similarity: sublinear TF-IDF, burstiness filter, mutual kNN."""
    params = params or GraphIndexParams()
    case_stats = case_stats or {}
    pos_stats = pos_stats or {}
    by_id = {d.document_id: d for d in docs}
    if len(doc_term_counts) < 1:
        return [], {"scores": [], "bugfix": "empty"}

    name_stems, name_bases = collect_lex_name_exclusions(case_stats, pos_stats)

    # Aggregate cleaned terms per work (canonical docs only expected)
    work_counts: dict[str, Counter[str]] = defaultdict(Counter)
    work_rep: dict[str, str] = {}  # work_key → representative document_id
    all_works = {d.work_key for d in docs}
    for doc_id, counts in doc_term_counts.items():
        meta = by_id.get(doc_id)
        if not meta:
            continue
        wk = meta.work_key
        work_rep.setdefault(wk, doc_id)
        for t, c in counts.items():
            if is_lex_proper_token(
                t, case_stats, name_stems=name_stems, name_bases=name_bases, pos_stats=pos_stats
            ):
                continue
            work_counts[wk][t] += int(c)

    # IDF / df caps use all works in the layer (including sparse ones)
    n_works = max(1, len(all_works) or len(work_counts))
    if len(work_counts) < 2:
        return [], {"scores": [], "bugfix": "n_works<2"}

    df_work: Counter[str] = Counter()
    cf_work: Counter[str] = Counter()
    for counts in work_counts.values():
        for t, c in counts.items():
            df_work[t] += 1
            cf_work[t] += int(c)

    max_df = max(2, int(0.30 * n_works))
    kept_terms: set[str] = set()
    dropped_burst: list[tuple[float, str, int, float]] = []
    kept_burst: list[tuple[float, str, int, float]] = []
    for t, dw in df_work.items():
        if dw < 2 or dw > max_df:
            continue
        cf = int(cf_work.get(t, 0))
        exp = _expected_df(n_works, cf)
        ratio = (dw / exp) if exp > 0 else 1.0
        # style-like: df close to random expectation → drop
        if ratio >= params.lex_burst_max_ratio and dw >= max(3, int(0.05 * n_works)):
            dropped_burst.append((ratio, t, dw, exp))
            continue
        kept_terms.add(t)
        kept_burst.append((ratio, t, dw, exp))

    dropped_burst.sort(reverse=True)
    kept_burst.sort()

    # Work vectors: sublinear tf * idf, top-M
    vectors: dict[str, dict[str, float]] = {}
    inv: dict[str, list[str]] = defaultdict(list)
    for wk, counts in work_counts.items():
        scored: list[tuple[float, str]] = []
        for t, c in counts.items():
            if t not in kept_terms:
                continue
            dw = df_work[t]
            tf = 1.0 + math.log(float(c))
            idf = math.log((n_works + 1) / (dw + 1)) + 1.0
            scored.append((tf * idf, t))
        scored.sort(reverse=True)
        top = scored[: params.lex_top_m]
        if not top:
            continue
        vec = {t: s for s, t in top}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors[wk] = {t: v / norm for t, v in vec.items()}
        for t in vectors[wk]:
            inv[t].append(wk)

    pair_best: dict[tuple[str, str], tuple[float, list[str]]] = {}
    for t, members in inv.items():
        mem = sorted(set(members))
        if len(mem) > 80:
            continue
        for i in range(len(mem)):
            for j in range(i + 1, len(mem)):
                a, b = mem[i], mem[j]
                if counter is not None:
                    counter.comparisons += 1
                va, vb = vectors.get(a), vectors.get(b)
                if not va or not vb:
                    continue
                shared = set(va) & set(vb)
                if len(shared) < 2:
                    continue
                contrib = {x: va[x] * vb[x] for x in shared}
                score = sum(contrib.values())
                if score <= 0:
                    continue
                # Reject only when a single term clearly dominates a tiny overlap
                if len(shared) == 2 and max(contrib.values()) > 0.85 * score:
                    continue
                key = (a, b) if a < b else (b, a)
                prev = pair_best.get(key)
                terms = sorted(shared, key=lambda x: -contrib[x])[:8]
                if not prev or score > prev[0]:
                    pair_best[key] = (score, terms)

    scores = sorted(s for s, _ in pair_best.values())
    thresh = params.lex_min_score
    if scores:
        p35 = scores[max(0, int(0.35 * (len(scores) - 1)))]
        if p35 > thresh:
            thresh = min(0.15, max(thresh, round(p35, 3)))

    # Mutual kNN among works
    neigh: dict[str, list[tuple[float, str, list[str]]]] = defaultdict(list)
    for (a, b), (score, terms) in pair_best.items():
        neigh[a].append((score, b, terms))
        neigh[b].append((score, a, terms))
    for wk in neigh:
        neigh[wk].sort(key=lambda x: (-x[0], x[1]))
        neigh[wk] = neigh[wk][: params.lex_top_k]

    mutual: set[tuple[str, str]] = set()
    for a, lst in neigh.items():
        for score, b, terms in lst:
            if any(x[1] == a for x in neigh.get(b) or []):
                key = (a, b) if a < b else (b, a)
                if pair_best.get(key, (0.0, []))[0] >= thresh:
                    mutual.add(key)

    edges: list[Edge] = []
    weak_edges = 0
    for a, b in sorted(mutual):
        score, terms = pair_best[(a, b)]
        ra, rb = work_rep.get(a), work_rep.get(b)
        if not ra or not rb:
            continue
        edges.append(
            Edge(
                source=ra,
                target=rb,
                type="lex_similar",
                weight=round(float(score), 6),
                provenance={
                    "terms": terms,
                    "shared_n": len(terms),
                    "work_a": a,
                    "work_b": b,
                    "level": "work",
                },
            )
        )

    # Orphan works: one weak edge to best neighbor if score ≥ floor
    linked_works: set[str] = set()
    for e in edges:
        pa = by_id.get(e.source)
        pb = by_id.get(e.target)
        if pa:
            linked_works.add(pa.work_key)
        if pb:
            linked_works.add(pb.work_key)

    for wk in work_counts:
        if wk in linked_works:
            continue
        best: tuple[float, str, list[str]] | None = None
        for (a, b), (score, terms) in pair_best.items():
            if a != wk and b != wk:
                continue
            other = b if a == wk else a
            if best is None or score > best[0]:
                best = (score, other, terms)
        if best is None or best[0] < params.lex_weak_floor:
            continue
        score, other, terms = best
        ra, rb = work_rep.get(wk), work_rep.get(other)
        if not ra or not rb:
            continue
        edges.append(
            Edge(
                source=ra,
                target=rb,
                type="lex_similar",
                weight=round(float(score), 6),
                provenance={
                    "terms": terms,
                    "shared_n": len(terms),
                    "work_a": wk,
                    "work_b": other,
                    "level": "work",
                    "weak": True,
                },
            )
        )
        weak_edges += 1
        linked_works.add(wk)

    hist = Counter(round(s, 2) for s in scores)
    debug = {
        "bugfix": (
            "Work-level vectors: 1+log tf, IDF over works, df∈[2,30%], "
            "burstiness drops style terms, mutual kNN, weak floor for orphans."
        ),
        "score_count": len(scores),
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "score_hist": dict(sorted(hist.items())[:40]),
        "edges": len(edges),
        "weak_edges": weak_edges,
        "threshold": thresh,
        "threshold_requested": params.lex_min_score,
        "weak_floor": params.lex_weak_floor,
        "comparisons": counter.comparisons if counter else None,
        "name_stems_sample": sorted(name_stems)[:30],
        "burst_dropped_top30": [
            {"term": t, "df": dw, "expected_df": round(exp, 2), "ratio": round(r, 3)}
            for r, t, dw, exp in dropped_burst[:30]
        ],
        "burst_kept_top30": [
            {"term": t, "df": dw, "expected_df": round(exp, 2), "ratio": round(r, 3)}
            for r, t, dw, exp in kept_burst[:30]
        ],
        "n_works": n_works,
        "kept_terms": len(kept_terms),
        "dropped_burst_n": len(dropped_burst),
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


def _list_query_markers() -> tuple[str, ...]:
    return tuple_field("list_query_markers") or (
        "перечисли",
        "перечислите",
        "какие произведения",
        "какой список",
        "список",
        "назови все",
        "назовите все",
        "все произведения",
    )


def is_list_query(text: str | None) -> bool:
    n = norm_ru(text or "")
    return any(m in n for m in _list_query_markers())


def citations_look_alphabetical(labels: list[str]) -> bool:
    """True if ≥4 labels are already sorted by filename/title (list-answer artifact)."""
    labs = [str(x).strip() for x in labels if str(x).strip()]
    if len(labs) < 4:
        return False
    low = [norm_ru(x) for x in labs]
    return low == sorted(low) or labs == sorted(labs)


def layer_e_co_cited(
    citation_lists: Iterable[Any],
    *,
    include_eval: bool = False,
    max_sources: int = 6,
    weight_scale: float = 0.5,
) -> tuple[list[Edge], dict[str, Any]]:
    """citation_lists items: (doc_ids, is_eval) or dict with ids/labels/user_text/is_eval."""
    live: Counter[tuple[str, str]] = Counter()
    ev: Counter[tuple[str, str]] = Counter()
    dropped = {"too_many": 0, "alphabetical": 0, "list_query": 0, "accepted": 0}

    for item in citation_lists:
        labels: list[str] = []
        user_text = ""
        is_eval = False
        cites: list[str] = []
        if isinstance(item, dict):
            cites = [str(x) for x in (item.get("ids") or item.get("doc_ids") or []) if x]
            labels = [str(x) for x in (item.get("labels") or []) if x]
            user_text = str(item.get("user_text") or item.get("question") or "")
            is_eval = bool(item.get("is_eval"))
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            cites = list(item[0] or [])
            is_eval = bool(item[1])
            if len(item) >= 3 and isinstance(item[2], dict):
                labels = list(item[2].get("labels") or [])
                user_text = str(item[2].get("user_text") or "")
        else:
            continue
        ids = list(dict.fromkeys(str(x) for x in cites if x))
        if len(ids) < 2:
            continue
        if len(ids) > max_sources:
            dropped["too_many"] += 1
            continue
        if is_list_query(user_text):
            dropped["list_query"] += 1
            continue
        if citations_look_alphabetical(labels or ids):
            dropped["alphabetical"] += 1
            continue
        dropped["accepted"] += 1
        # pair without forcing alphabetical sort of ids (preserve answer order noise out)
        bucket = ev if is_eval else live
        ordered = sorted(ids)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                bucket[(ordered[i], ordered[j])] += 1

    edges: list[Edge] = []
    for (a, b), w in live.items():
        edges.append(
            Edge(
                source=a,
                target=b,
                type="co_cited",
                weight=float(w) * float(weight_scale),
                provenance={
                    "co_citation_answers": w,
                    "channel": "live",
                    "weight_scale": weight_scale,
                },
            )
        )
    if include_eval:
        for (a, b), w in ev.items():
            edges.append(
                Edge(
                    source=a,
                    target=b,
                    type="co_cited_eval",
                    weight=float(w) * 0.5 * float(weight_scale),
                    provenance={"co_citation_answers": w, "channel": "eval"},
                )
            )
    return edges, dropped


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
            cids = (e.provenance or {}).get("chunk_ids") or []
            slot["chunks"] += len(cids) if cids else (1 if e.weight else 0)
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
                weight=round(float(slot["weight"]), 4),
                provenance={
                    "examples": slot["examples"],
                    "chunk_mentions": int(slot["chunks"]),
                    "weight_sum": round(float(slot["weight"]), 4),
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
    content_types_no_cocited = {
        "refers_to",
        "shared_entity",
        "lex_similar",
        "mentions_author",
    }
    structural_types = {"wrote", "in_theme", "part_of", "work_meta", "contains"}
    content_linked: set[str] = set()
    content_linked_no_cc: set[str] = set()
    structural_linked: set[str] = set()
    for e in edges:
        if e.type in content_types:
            content_linked.add(e.source)
            if not str(e.target).startswith("author:"):
                content_linked.add(e.target)
        if e.type in content_types_no_cocited:
            content_linked_no_cc.add(e.source)
            if not str(e.target).startswith("author:"):
                content_linked_no_cc.add(e.target)
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
    isolated_content_no_cc_ids = [
        d.document_id for d in docs if d.document_id not in content_linked_no_cc
    ]
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
    work_linked_no_cc: set[str] = set()
    for e in edges:
        if e.type.startswith("work_") and e.type != "work_meta":
            endpoints = {e.source.removeprefix("work:")}
            if not str(e.target).startswith("author:"):
                endpoints.add(e.target.removeprefix("work:"))
            work_linked |= endpoints
            if e.type not in ("work_co_cited", "work_co_cited_eval"):
                work_linked_no_cc |= endpoints
        if e.type in ("refers_to", "shared_entity", "lex_similar", "co_cited", "co_cited_eval"):
            sa = by_id.get(e.source)
            sb = by_id.get(e.target)
            if sa:
                work_linked.add(sa.work_key)
                if e.type not in ("co_cited", "co_cited_eval"):
                    work_linked_no_cc.add(sa.work_key)
            if sb:
                work_linked.add(sb.work_key)
                if e.type not in ("co_cited", "co_cited_eval"):
                    work_linked_no_cc.add(sb.work_key)
    isolated_works = [wk for wk in works if wk not in work_linked]
    isolated_works_no_cc = [wk for wk in works if wk not in work_linked_no_cc]
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
        "isolated_content_no_cocited": len(isolated_content_no_cc_ids),
        "isolated_structural": len(isolated_structural_ids),
        "isolated_content_top20": isolated_content_top,
        "isolated_document_ids_sample": isolated_content_ids[:20],
        "isolated_works": len(isolated_works),
        "isolated_works_no_cocited": len(isolated_works_no_cc),
        "isolated_work_share": round(len(isolated_works) / max(1, len(works)), 4),
        "isolated_work_share_no_cocited": round(
            len(isolated_works_no_cc) / max(1, len(works)), 4
        ),
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
                "contained_in": d.contained_in,
                "relation": d.relation,
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

    # E8 noop: unchanged corpus + params → return current index in ~1s (even if force)
    ptr = gcache.read_current_pointer(tenant_id) or {}
    if (
        ptr.get("fingerprint") == fp
        and ptr.get("params_hash") == ph
        and not ptr.get("stale")
        and ptr.get("index_file")
    ):
        prev = read_index(gcache.graph_dir() / str(ptr["index_file"]))
        if prev and prev.get("version") == INDEX_VERSION and prev.get("params_hash") in (None, ph):
            prev = dict(prev)
            prev["built_ms"] = int((time.perf_counter() - t0) * 1000)
            st = dict(prev.get("stats") or {})
            st["build_mode"] = "noop"
            st["lifecycle"] = {
                "added": 0,
                "changed": 0,
                "removed": 0,
                "cached": int(st.get("documents") or len(prev.get("documents") or [])),
            }
            st["stale"] = False
            prev["stats"] = st
            gcache.write_build_status(
                tenant_id,
                {
                    "status": "ready",
                    "mode": "noop",
                    "fingerprint": fp,
                    "params_hash": ph,
                    "built_ms": prev["built_ms"],
                    "stale": False,
                    "index_file": ptr.get("index_file"),
                },
            )
            log.info("graph_index_noop", tenant_id=str(tenant_id), fingerprint=fp)
            return prev

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
    override_applied = govr.apply_overrides(docs)
    if override_applied:
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

    # E1: duplicates + contained_in (minhash/LSH); DF only on canonical
    texts_by_id = {
        did: "\n".join(ch.get("text") or "" for ch in (art.get("clean_chunks") or []))
        for did, art in artifacts.items()
    }
    dup_map = gdup.detect_relations(docs, texts_by_id)
    canon_ids = gdup.canonical_doc_ids(dup_map)
    for meta in docs:
        info = dup_map.get(meta.document_id) or {}
        meta.canonical = bool(info.get("canonical", True))
        meta.duplicate_of = info.get("duplicate_of")
        meta.contained_in = info.get("contained_in")
        meta.relation = str(info.get("relation") or "canonical")

    # Rebuild global stats from canonical docs only (no duplicates / contained)
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

    # Textual layers use canonical docs only; contained inherit at work level later
    docs_canon = [d for d in docs if d.document_id in canon_ids]
    chunk_rows_canon = [r for r in chunk_rows if r[0] in canon_ids]
    doc_entities_canon = {k: v for k, v in doc_entities.items() if k in canon_ids}
    doc_entity_chunks_canon = {k: v for k, v in doc_entity_chunks.items() if k in canon_ids}
    doc_term_counts_canon = {k: v for k, v in doc_term_counts.items() if k in canon_ids}

    edges: list[Edge] = []
    t_l = time.perf_counter()
    edges.extend(layer_a_edges(docs))
    edges.extend(gdup.contains_edges_from_map(dup_map, edge_cls=Edge))
    layer_ms["layer_a"] = int((time.perf_counter() - t_l) * 1000)

    t_l = time.perf_counter()
    edges.extend(layer_mentions_author(docs=docs_canon, chunk_iter=chunk_rows_canon))
    layer_ms["mentions_author"] = int((time.perf_counter() - t_l) * 1000)

    t_l = time.perf_counter()
    # Title catalog = all docs (nested works visible); sources = non-contained only.
    # Container→nested refers_to rewritten to structural contains.
    source_ids = {
        d.document_id
        for d in docs
        if not (dup_map.get(d.document_id) or {}).get("contained_in")
        and not (dup_map.get(d.document_id) or {}).get("duplicate_of")
    }
    chunk_rows_refers = [r for r in chunk_rows if r[0] in source_ids]
    refers = layer_b_refers_to(docs=docs, chunk_iter=chunk_rows_refers, params=params)
    edges.extend(gdup.rewrite_refers_to_contains(refers, dup_map, edge_cls=Edge))
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
    cite_lists: list[dict[str, Any]] = []
    msgs = (
        db.query(ChatMessage.id, ChatMessage.citations, ChatMessage.session_id, ChatSession.title)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .filter(ChatMessage.tenant_id == tenant_id, ChatMessage.role == "assistant")
        .all()
    )
    # optional preceding user text per session (best-effort)
    user_by_session: dict[str, str] = {}
    try:
        umsgs = (
            db.query(ChatMessage.session_id, ChatMessage.content)
            .filter(ChatMessage.tenant_id == tenant_id, ChatMessage.role == "user")
            .order_by(ChatMessage.created_at.desc())
            .limit(500)
            .all()
        )
        for sid, content in umsgs:
            sk = str(sid)
            if sk not in user_by_session:
                user_by_session[sk] = content or ""
    except Exception:  # noqa: BLE001
        user_by_session = {}
    for _mid, citations, session_id, title in msgs:
        ids = extract_doc_ids_from_citations(citations)
        if len(ids) < 2:
            continue
        labels: list[str] = []
        if isinstance(citations, list):
            for item in citations:
                if isinstance(item, dict):
                    labels.append(
                        str(
                            item.get("filename")
                            or item.get("title")
                            or item.get("original_name")
                            or item.get("document_id")
                            or ""
                        )
                    )
        cite_lists.append(
            {
                "ids": ids,
                "labels": labels,
                "user_text": user_by_session.get(str(session_id), ""),
                "is_eval": is_eval_chat_session(title),
            }
        )
    co_edges, co_dropped = layer_e_co_cited(
        cite_lists,
        include_eval=params.include_eval_cocited,
        max_sources=params.co_cited_max_sources,
        weight_scale=params.co_cited_weight_scale,
    )
    edges.extend(co_edges)
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
            "co_cited_filter": co_dropped,
            "overrides_applied": override_applied[:40],
            "duplicates": gdup.relation_stats(dup_map),
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
