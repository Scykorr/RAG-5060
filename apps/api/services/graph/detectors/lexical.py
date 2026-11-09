"""Lexical similarity between documents via token Jaccard on representative chunks.

Honest alternative while Qdrant holds zero vectors (not embedding cosine).
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.orm import Session

from apps.api.models import Chunk, Document, DocumentVersion
from apps.api.services.graph.detectors.base import EvidenceCandidate, RelationCandidate
from apps.api.services.graph.settings import get_graph_limits

_TOKEN = re.compile(r"[а-яёa-z0-9]{4,}", re.I)
_STOP = {
    "этот",
    "эта",
    "это",
    "того",
    "также",
    "который",
    "которая",
    "которые",
    "быть",
    "был",
    "была",
    "были",
    "есть",
    "как",
    "что",
    "для",
    "или",
    "при",
    "без",
    "над",
    "под",
    "все",
    "всё",
    "его",
    "её",
    "их",
    "она",
    "они",
    "он",
    "мы",
    "вы",
    "то",
    "же",
    "бы",
    "ли",
    "не",
    "ни",
    "на",
    "по",
    "из",
    "от",
    "до",
    "за",
    "со",
    "об",
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
}


def tokenize(text: str) -> set[str]:
    toks = {_t.casefold() for _t in _TOKEN.findall(text or "")}
    return {t for t in toks if t not in _STOP and not t.isdigit()}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


class LexicalSimilarityDetector:
    detector_id = "lexical_jaccard_v1"
    algorithm_version = "1"
    is_enabled = True

    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
        self.limits = get_graph_limits()

    def detect(self, document_id: UUID, context: dict) -> list[RelationCandidate]:
        ver: DocumentVersion | None = context.get("version")
        chunks: list[Chunk] = context.get("chunks") or []
        if not ver or not chunks:
            return []

        reps = _pick_representative(chunks, self.limits.representative_chunks)
        if not reps:
            return []
        src_tokens = tokenize("\n".join(c.text for c in reps))
        if len(src_tokens) < 12:
            return []

        # Candidate docs: scan limited corpus of active docs (bounded)
        others = (
            self.db.query(Document)
            .filter(
                Document.tenant_id == self.tenant_id,
                Document.deleted_at.is_(None),
                Document.status != "deleted",
                Document.active_version_id.is_not(None),
                Document.id != document_id,
            )
            .order_by(Document.created_at.desc())
            .limit(400)
            .all()
        )
        scored: list[tuple[float, Document, DocumentVersion, Chunk, Chunk]] = []
        for doc in others:
            tver = self.db.get(DocumentVersion, doc.active_version_id)
            if not tver:
                continue
            tchunks = (
                self.db.query(Chunk)
                .filter(Chunk.version_id == tver.id, Chunk.tenant_id == self.tenant_id)
                .order_by(Chunk.ordinal.asc())
                .limit(24)
                .all()
            )
            treps = _pick_representative(tchunks, self.limits.representative_chunks)
            if not treps:
                continue
            best = 0.0
            best_pair: tuple[Chunk, Chunk] | None = None
            for sc in reps:
                st = tokenize(sc.text)
                if len(st) < 8:
                    continue
                for tc in treps:
                    tt = tokenize(tc.text)
                    sc_score = jaccard(st, tt)
                    if sc_score > best:
                        best = sc_score
                        best_pair = (sc, tc)
            if best >= self.limits.lexical_min_score and best_pair:
                scored.append((best, doc, tver, best_pair[0], best_pair[1]))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_k = self.limits.neighbor_top_k
        out: list[RelationCandidate] = []
        for score, doc, tver, sc, tc in scored[:top_k]:
            out.append(
                RelationCandidate(
                    source_document_id=document_id,
                    target_document_id=doc.id,
                    source_version_id=ver.id,
                    target_version_id=tver.id,
                    relation_type="SEMANTIC_SIMILAR",
                    directed=False,
                    origin="SYSTEM_RULE",
                    review_state="PROPOSED",
                    score=round(float(score), 4),
                    score_method="token_jaccard_v1",
                    algorithm_name=self.detector_id,
                    algorithm_version=self.algorithm_version,
                    detector_key=f"{self.detector_id}:doc",
                    evidence=[
                        EvidenceCandidate(
                            source_chunk_id=sc.id,
                            target_chunk_id=tc.id,
                            source_version_id=ver.id,
                            target_version_id=tver.id,
                            page=sc.page_start,
                            method="token_jaccard_v1",
                            score_raw=score,
                            score_normalized=score,
                            explanation=(
                                "Лексическое сходство чанков (Jaccard). "
                                "Не embedding cosine и не доказательство истинности."
                            ),
                        )
                    ],
                )
            )
        return out


def _pick_representative(chunks: list[Chunk], n: int) -> list[Chunk]:
    if not chunks:
        return []
    # Prefer longer non-boilerplate chunks spread across the doc
    ranked = sorted(chunks, key=lambda c: len(c.text or ""), reverse=True)
    picked: list[Chunk] = []
    seen_ord: set[int] = set()
    for c in ranked:
        if len(c.text or "") < 80:
            continue
        # skip near-duplicate ordinals
        if any(abs(c.ordinal - o) <= 1 for o in seen_ord):
            continue
        picked.append(c)
        seen_ord.add(c.ordinal)
        if len(picked) >= n:
            break
    if not picked and chunks:
        picked = chunks[: min(n, len(chunks))]
    return picked
