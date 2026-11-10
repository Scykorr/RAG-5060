"""Lexical document connectivity index (no Qdrant, no embeddings, no UI).

Builds a JSON index under data_root()/graph/ from active document chunks:
layers A (filename scaffold), B (refers_to), C (shared_entity), D (lex_similar), E (co_cited).
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

from apps.api.models import ChatMessage, Chunk, Document, DocumentVersion
from apps.api.services import query_intent as qi
from apps.api.services.quality_state import corpus_fingerprint
from apps.api.services.storage import data_root
from apps.api.services.text_entities import (
    ENTITY_DENY,
    REFERS_STOP_PHRASES,
    STOP,
    extract_entity_candidates,
    norm_ru,
    tokenize,
)

log = structlog.get_logger()

INDEX_VERSION = 1

# Defaults tuned on fixtures; adjustable via GraphIndexParams.
DEFAULT_CHUNK_BATCH = 200
DEFAULT_ENTITY_TOP_N = 20
DEFAULT_SHARED_MIN = 2
DEFAULT_SHARED_TOP_K = 8
DEFAULT_LEX_TOP_M = 40
DEFAULT_LEX_TOP_K = 5
DEFAULT_LEX_MIN_SCORE = 0.12
DEFAULT_REFERS_PROVENANCE_CHUNKS = 3


@dataclass
class GraphIndexParams:
    chunk_batch: int = DEFAULT_CHUNK_BATCH
    entity_top_n: int = DEFAULT_ENTITY_TOP_N
    shared_min_entities: int = DEFAULT_SHARED_MIN
    shared_top_k: int = DEFAULT_SHARED_TOP_K
    lex_top_m: int = DEFAULT_LEX_TOP_M
    lex_top_k: int = DEFAULT_LEX_TOP_K
    lex_min_score: float = DEFAULT_LEX_MIN_SCORE
    refers_provenance_chunks: int = DEFAULT_REFERS_PROVENANCE_CHUNKS

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_batch": self.chunk_batch,
            "entity_top_n": self.entity_top_n,
            "shared_min_entities": self.shared_min_entities,
            "shared_top_k": self.shared_top_k,
            "lex_top_m": self.lex_top_m,
            "lex_top_k": self.lex_top_k,
            "lex_min_score": self.lex_min_score,
            "refers_provenance_chunks": self.refers_provenance_chunks,
        }


@dataclass
class DocMeta:
    document_id: str
    filename: str
    theme: str
    author_key: str | None
    author_ru: str | None
    title: str
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


# ---------------------------------------------------------------------------
# Layer A — filename scaffold (pure)
# ---------------------------------------------------------------------------

_AUTHOR_RU_BY_KEY: dict[str, str] = {}
for _ru, _key in qi.AUTHOR_ALIASES.items():
    _AUTHOR_RU_BY_KEY.setdefault(_key, _ru)


def theme_from_filename(name: str) -> str:
    """classic_0001_… → classic_; unknown → Прочее."""
    base = Path(name or "").name
    m = re.match(r"^([A-Za-z][A-Za-z0-9]*)_", base)
    if not m:
        return "Прочее"
    prefix = m.group(1).lower()
    if prefix in ("tmp", "file", "doc", "new", "copy"):
        return "Прочее"
    return f"{prefix}_"


def author_from_filename(name: str) -> tuple[str | None, str | None]:
    """Return (author_key Latin, author_ru) from classic_NNNN_Author_Title…"""
    base = Path(name or "").name.rsplit(".", 1)[0]
    base = re.sub(r"_[0-9a-f]{8,}$", "", base, flags=re.I)
    parts = base.split("_")
    key: str | None = None
    if len(parts) >= 3 and parts[0].lower().startswith("classic") and re.fullmatch(r"\d{3,}", parts[1] or ""):
        key = parts[2]
    elif len(parts) >= 3 and re.fullmatch(r"[A-Za-z][A-Za-z\-]+", parts[1] or ""):
        # prefix_Author_Title
        key = parts[1]
    elif len(parts) >= 4 and re.fullmatch(r"[A-Za-z][A-Za-z\-]+", parts[2] or ""):
        key = parts[2]
    if not key or len(key) < 2:
        return None, None
    if key[0].islower() and key.lower() in ("фрагмент", "fragment"):
        return None, None
    # Prefer known catalog authors
    ru = _AUTHOR_RU_BY_KEY.get(key) or _AUTHOR_RU_BY_KEY.get(key.capitalize())
    if ru:
        return key if key[0].isupper() else key.capitalize(), ru
    # Latin-looking token only
    if re.fullmatch(r"[A-Za-z][A-Za-z\-]+", key):
        return key[0].upper() + key[1:], None
    return None, None


def parse_doc_meta(
    *,
    document_id: str,
    filename: str,
    chunk_count: int = 0,
    created_at: str | None = None,
) -> DocMeta:
    theme = theme_from_filename(filename)
    author_key, author_ru = author_from_filename(filename)
    title = qi.parse_title_from_filename(filename) or Path(filename).stem
    if not title.strip():
        title = Path(filename).stem or document_id
    return DocMeta(
        document_id=str(document_id),
        filename=filename,
        theme=theme,
        author_key=author_key,
        author_ru=author_ru,
        title=title,
        chunk_count=chunk_count,
        created_at=created_at,
    )


def layer_a_edges(docs: list[DocMeta]) -> list[Edge]:
    edges: list[Edge] = []
    for d in docs:
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
    return edges


# ---------------------------------------------------------------------------
# Layer B — refers_to (pure)
# ---------------------------------------------------------------------------

def _short_title_forms(title: str) -> list[str]:
    """Significant single tokens from title (e.g. Онегин)."""
    forms: list[str] = []
    for tok in re.findall(r"[A-Za-zА-Яа-яЁё]{6,}", title or "", flags=re.UNICODE):
        n = norm_ru(tok)
        if n in STOP or n in ENTITY_DENY or n in REFERS_STOP_PHRASES:
            continue
        forms.append(tok)
    return forms


def build_refers_phrase_index(docs: list[DocMeta]) -> list[tuple[str, str, str]]:
    """List of (norm_phrase, document_id, surface) sorted longest-first."""
    items: list[tuple[str, str, str]] = []
    seen_pair: set[tuple[str, str]] = set()

    def add(surface: str, doc_id: str) -> None:
        s = (surface or "").strip(" .,:;«»\"'")
        if not s:
            return
        n = norm_ru(s)
        if len(n) < 2:
            return
        # title ≥2 words OR ≥6 chars
        words = n.split()
        if len(words) < 2 and len(n) < 6:
            return
        if n in REFERS_STOP_PHRASES or n in STOP or n in ENTITY_DENY:
            return
        key = (n, doc_id)
        if key in seen_pair:
            return
        seen_pair.add(key)
        items.append((n, doc_id, s))

    for d in docs:
        add(d.title, d.document_id)
        for form in _short_title_forms(d.title):
            add(form, d.document_id)
        if d.author_key:
            add(d.author_key, d.document_id)
        if d.author_ru:
            add(d.author_ru, d.document_id)
            # aliases pointing to same key
            for ru, key in qi.AUTHOR_ALIASES.items():
                if key == d.author_key:
                    add(ru, d.document_id)
        elif d.author_key:
            for ru, key in qi.AUTHOR_ALIASES.items():
                if key == d.author_key:
                    add(ru, d.document_id)

    items.sort(key=lambda x: (-len(x[0]), x[0], x[1]))
    return items


def layer_b_refers_to(
    *,
    docs: list[DocMeta],
    chunk_iter: Iterable[tuple[str, str, str]],
    # (document_id, chunk_id, text)
    params: GraphIndexParams | None = None,
) -> list[Edge]:
    """A→B when chunks of A mention a phrase of B. No self-links."""
    params = params or GraphIndexParams()
    phrases = build_refers_phrase_index(docs)
    if not phrases:
        return []

    # doc_id → phrase_norm → {chunk_ids, surface}
    hits: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {"chunks": [], "surface": "", "target": ""}))

    for doc_id, chunk_id, text in chunk_iter:
        body = norm_ru(text or "")
        if not body:
            continue
        matched_spans: list[tuple[int, int]] = []
        for phrase_n, target_id, surface in phrases:
            if target_id == doc_id:
                continue
            # skip if overlaps already matched longer phrase
            start = 0
            while True:
                idx = body.find(phrase_n, start)
                if idx < 0:
                    break
                end = idx + len(phrase_n)
                # word-ish boundary: not alnum on sides
                left_ok = idx == 0 or not body[idx - 1].isalnum()
                right_ok = end >= len(body) or not body[end].isalnum()
                if left_ok and right_ok:
                    overlap = any(not (end <= a or idx >= b) for a, b in matched_spans)
                    if not overlap:
                        matched_spans.append((idx, end))
                        bucket = hits[doc_id][f"{target_id}::{phrase_n}"]
                        bucket["target"] = target_id
                        bucket["surface"] = surface
                        if chunk_id not in bucket["chunks"]:
                            bucket["chunks"].append(chunk_id)
                        break
                start = idx + 1

    edges: list[Edge] = []
    for src, by_phrase in hits.items():
        # aggregate by target
        by_target: dict[str, dict[str, Any]] = {}
        for _k, info in by_phrase.items():
            tid = info["target"]
            agg = by_target.setdefault(tid, {"chunks": [], "phrases": []})
            for cid in info["chunks"]:
                if cid not in agg["chunks"]:
                    agg["chunks"].append(cid)
            if info["surface"] not in agg["phrases"]:
                agg["phrases"].append(info["surface"])
        for tid, agg in by_target.items():
            edges.append(
                Edge(
                    source=src,
                    target=tid,
                    type="refers_to",
                    weight=float(len(agg["chunks"])),
                    provenance={
                        "chunk_ids": agg["chunks"][: params.refers_provenance_chunks],
                        "phrase": agg["phrases"][0] if agg["phrases"] else "",
                        "phrases": agg["phrases"][:5],
                    },
                )
            )
    return edges


# ---------------------------------------------------------------------------
# Layer C — shared_entity (pure)
# ---------------------------------------------------------------------------

def layer_c_shared_entity(
    *,
    doc_entities: dict[str, list[str]],
    doc_entity_chunks: dict[str, dict[str, list[str]]] | None = None,
    params: GraphIndexParams | None = None,
) -> list[Edge]:
    """Edges when ≥ shared_min common entities; top-k neighbours per doc."""
    params = params or GraphIndexParams()
    doc_entity_chunks = doc_entity_chunks or {}
    # df
    df: Counter[str] = Counter()
    norm_sets: dict[str, set[str]] = {}
    surface_of: dict[str, str] = {}
    for doc_id, ents in doc_entities.items():
        ns: set[str] = set()
        for e in ents[: params.entity_top_n]:
            n = norm_ru(e)
            if len(n) < 3 or n in STOP or n in ENTITY_DENY:
                continue
            ns.add(n)
            surface_of.setdefault(n, e)
        for n in ns:
            df[n] += 1
        norm_sets[doc_id] = ns

    # keep entities with df≥2
    valid = {e for e, c in df.items() if c >= 2}
    for doc_id in list(norm_sets.keys()):
        norm_sets[doc_id] = {e for e in norm_sets[doc_id] if e in valid}

    doc_ids = list(norm_sets.keys())
    # candidate pairs via inverted index
    inv: dict[str, list[str]] = defaultdict(list)
    for doc_id, ents in norm_sets.items():
        for e in ents:
            inv[e].append(doc_id)

    pair_common: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e, members in inv.items():
        if len(members) < 2:
            continue
        mem = sorted(set(members))
        for i in range(len(mem)):
            for j in range(i + 1, len(mem)):
                a, b = mem[i], mem[j]
                pair_common[(a, b)].add(e)

    # per-doc top-k
    neigh: dict[str, list[tuple[float, str, set[str]]]] = defaultdict(list)
    for (a, b), common in pair_common.items():
        if len(common) < params.shared_min_entities:
            continue
        w = float(len(common))
        neigh[a].append((w, b, common))
        neigh[b].append((w, a, common))

    edges: list[Edge] = []
    seen_undirected: set[tuple[str, str]] = set()
    for src, lst in neigh.items():
        lst.sort(key=lambda x: (-x[0], x[1]))
        for w, tgt, common in lst[: params.shared_top_k]:
            key = tuple(sorted((src, tgt)))
            if key in seen_undirected:
                continue
            seen_undirected.add(key)
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
                    weight=w,
                    provenance={
                        "entities": [surface_of.get(e, e) for e in sorted(common)[:10]],
                        "chunk_ids": chunk_ids[:3],
                    },
                )
            )
    return edges


# ---------------------------------------------------------------------------
# Layer D — lex_similar TF-IDF (pure)
# ---------------------------------------------------------------------------

class LexCandidateCounter:
    """Test hook: counts candidate pair evaluations (must stay ≪ N²)."""

    def __init__(self) -> None:
        self.comparisons = 0


def layer_d_lex_similar(
    *,
    doc_term_counts: dict[str, Counter[str]],
    params: GraphIndexParams | None = None,
    counter: LexCandidateCounter | None = None,
) -> list[Edge]:
    params = params or GraphIndexParams()
    n_docs = len(doc_term_counts)
    if n_docs < 2:
        return []

    df: Counter[str] = Counter()
    for counts in doc_term_counts.values():
        for t in counts:
            df[t] += 1

    # characteristic terms per doc (TF-IDF)
    char_terms: dict[str, dict[str, float]] = {}
    inv: dict[str, list[str]] = defaultdict(list)
    for doc_id, counts in doc_term_counts.items():
        total = sum(counts.values()) or 1
        scores: list[tuple[float, str]] = []
        for t, c in counts.items():
            # skip ultra-common
            if df[t] >= max(2, int(0.6 * n_docs)):
                continue
            tf = c / total
            idf = math.log((n_docs + 1) / (df[t] + 1)) + 1.0
            scores.append((tf * idf, t))
        scores.sort(reverse=True)
        top = scores[: params.lex_top_m]
        char_terms[doc_id] = {t: s for s, t in top}
        for _s, t in top:
            inv[t].append(doc_id)

    # candidates sharing rare terms
    pair_scores: dict[tuple[str, str], float] = defaultdict(float)
    pair_terms: dict[tuple[str, str], list[str]] = defaultdict(list)
    for t, members in inv.items():
        if len(members) < 2:
            continue
        # rare-ish
        if df[t] > max(3, int(0.35 * n_docs)):
            continue
        mem = sorted(set(members))
        for i in range(len(mem)):
            for j in range(i + 1, len(mem)):
                a, b = mem[i], mem[j]
                if counter is not None:
                    counter.comparisons += 1
                sa = char_terms[a].get(t, 0.0)
                sb = char_terms[b].get(t, 0.0)
                contrib = sa * sb
                if contrib <= 0:
                    continue
                key = (a, b)
                pair_scores[key] += contrib
                if t not in pair_terms[key]:
                    pair_terms[key].append(t)

    neigh: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for (a, b), score in pair_scores.items():
        if score < params.lex_min_score:
            continue
        neigh[a].append((score, b))
        neigh[b].append((score, a))

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for src, lst in neigh.items():
        lst.sort(key=lambda x: (-x[0], x[1]))
        for score, tgt in lst[: params.lex_top_k]:
            key = tuple(sorted((src, tgt)))
            if key in seen:
                continue
            seen.add(key)
            terms = pair_terms.get((key[0], key[1])) or pair_terms.get((key[1], key[0])) or []
            edges.append(
                Edge(
                    source=key[0],
                    target=key[1],
                    type="lex_similar",
                    weight=round(float(score), 6),
                    provenance={"terms": terms[:5]},
                )
            )
    return edges


# ---------------------------------------------------------------------------
# Layer E — co_cited (pure)
# ---------------------------------------------------------------------------

def layer_e_co_cited(
    citation_lists: Iterable[list[str]],
) -> list[Edge]:
    """Undirected edges when two doc ids co-occur in one assistant citation list."""
    pair_w: Counter[tuple[str, str]] = Counter()
    for cites in citation_lists:
        ids = sorted({str(x) for x in cites if x})
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pair_w[(ids[i], ids[j])] += 1
    return [
        Edge(
            source=a,
            target=b,
            type="co_cited",
            weight=float(w),
            provenance={"co_citation_answers": w},
        )
        for (a, b), w in pair_w.items()
    ]


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


# ---------------------------------------------------------------------------
# Assemble + cache
# ---------------------------------------------------------------------------

def assemble_index(
    *,
    tenant_id: str,
    fingerprint: str,
    docs: list[DocMeta],
    edges: list[Edge],
    params: GraphIndexParams,
    built_ms: int,
) -> dict[str, Any]:
    edge_counts: Counter[str] = Counter(e.type for e in edges)
    linked_docs = {e.source for e in edges if not e.source.startswith(("theme:", "author:"))}
    linked_docs |= {e.target for e in edges if not e.target.startswith(("theme:", "author:"))}
    # Also count theme/wrote as linking the document endpoint
    for e in edges:
        if e.type in ("in_theme", "wrote"):
            linked_docs.add(e.target)
    isolated = [d.document_id for d in docs if d.document_id not in linked_docs]
    # For connectivity stats, "isolated" = no B/C/D/E edges
    content_linked: set[str] = set()
    for e in edges:
        if e.type in ("refers_to", "shared_entity", "lex_similar", "co_cited"):
            content_linked.add(e.source)
            content_linked.add(e.target)
    isolated_content = [d.document_id for d in docs if d.document_id not in content_linked]

    themes = sorted({d.theme for d in docs})
    authors = sorted({d.author_key for d in docs if d.author_key})
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
                "author_key": d.author_key,
                "author_ru": d.author_ru,
                "title": d.title,
                "chunk_count": d.chunk_count,
                "created_at": d.created_at,
            }
            for d in docs
        ],
        "edges": [e.to_dict() for e in edges],
        "stats": {
            "documents": len(docs),
            "themes": len(themes),
            "authors": len(authors),
            "theme_list": themes,
            "author_list": authors,
            "edge_counts_by_type": dict(edge_counts),
            "isolated_documents": len(isolated_content),
            "isolated_document_ids_sample": isolated_content[:20],
        },
    }


def graph_dir() -> Path:
    p = data_root() / "graph"
    p.mkdir(parents=True, exist_ok=True)
    return p


def index_path(tenant_id: UUID | str, fingerprint: str) -> Path:
    return graph_dir() / f"{tenant_id}_{fingerprint}_index.json"


def purge_old_indexes(tenant_id: UUID | str, keep_fingerprint: str) -> int:
    removed = 0
    prefix = f"{tenant_id}_"
    suffix = "_index.json"
    for p in graph_dir().glob(f"{tenant_id}_*_index.json"):
        name = p.name
        if not name.startswith(prefix) or not name.endswith(suffix):
            continue
        mid = name[len(prefix) : -len(suffix)]
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
    rows = (
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
    return list(rows)


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
        if cached and cached.get("fingerprint") == fp:
            log.info("graph_index_cache_hit", tenant_id=str(tenant_id), fingerprint=fp)
            return cached

    rows = _active_documents(db, tenant_id)
    docs: list[DocMeta] = []
    version_ids: list[UUID] = []
    for doc, ver in rows:
        created = doc.created_at.isoformat() if doc.created_at else None
        meta = parse_doc_meta(
            document_id=str(doc.id),
            filename=ver.original_name,
            chunk_count=int(ver.indexed_chunk_count or 0),
            created_at=created,
        )
        docs.append(meta)
        version_ids.append(ver.id)

    edges: list[Edge] = []
    edges.extend(layer_a_edges(docs))

    # Stream chunks once: refers_to + entity/term accumulation
    doc_entities: dict[str, list[str]] = defaultdict(list)
    doc_entity_chunks: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    doc_term_counts: dict[str, Counter[str]] = defaultdict(Counter)
    chunk_total = 0

    # For refers_to we need another pass OR collect texts — collect per-doc texts lightly
    # Use single pass: accumulate for C/D and run B with same iterator by materializing batches.
    chunk_rows: list[tuple[str, str, str]] = []
    for item in _iter_chunks_batched(db, tenant_id, version_ids, params.chunk_batch):
        chunk_rows.append(item)
        chunk_total += 1
        doc_id, chunk_id, text = item
        ents = extract_entity_candidates([text], limit=params.entity_top_n)
        for e in ents:
            n = norm_ru(e)
            if e not in doc_entities[doc_id]:
                doc_entities[doc_id].append(e)
            if chunk_id not in doc_entity_chunks[doc_id][n]:
                doc_entity_chunks[doc_id][n].append(chunk_id)
        for tok in tokenize(text):
            doc_term_counts[doc_id][tok] += 1

    # Update chunk_count from actual
    counts_by_doc: Counter[str] = Counter(d for d, _, _ in chunk_rows)
    for d in docs:
        d.chunk_count = int(counts_by_doc.get(d.document_id, d.chunk_count))

    edges.extend(layer_b_refers_to(docs=docs, chunk_iter=chunk_rows, params=params))
    edges.extend(
        layer_c_shared_entity(
            doc_entities=dict(doc_entities),
            doc_entity_chunks={k: dict(v) for k, v in doc_entity_chunks.items()},
            params=params,
        )
    )
    edges.extend(layer_d_lex_similar(doc_term_counts=dict(doc_term_counts), params=params))

    # co_cited
    cite_lists: list[list[str]] = []
    msgs = (
        db.query(ChatMessage.citations)
        .filter(ChatMessage.tenant_id == tenant_id, ChatMessage.role == "assistant")
        .all()
    )
    for (citations,) in msgs:
        ids = extract_doc_ids_from_citations(citations)
        if len(ids) >= 2:
            cite_lists.append(ids)
    edges.extend(layer_e_co_cited(cite_lists))

    built_ms = int((time.perf_counter() - t0) * 1000)
    payload = assemble_index(
        tenant_id=str(tenant_id),
        fingerprint=fp,
        docs=docs,
        edges=edges,
        params=params,
        built_ms=built_ms,
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
        chunks=chunk_total,
        edge_counts=stats["edge_counts_by_type"],
        isolated=stats["isolated_documents"],
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
    # sort each group by weight desc, truncate
    out: dict[str, list[dict[str, Any]]] = {}
    for typ, items in grouped.items():
        items.sort(key=lambda x: (-float(x.get("weight") or 0), str(x.get("neighbor_id"))))
        out[typ] = items[:limit]
    return {"document_id": doc_id, "neighbors_by_type": out, "fingerprint": index.get("fingerprint")}
