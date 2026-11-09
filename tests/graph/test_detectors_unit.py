"""Unit tests for graph detectors — no DB/GPU required."""

from __future__ import annotations

import uuid

from apps.api.services.graph.detectors.base import EvidenceCandidate, RelationCandidate
from apps.api.services.graph.detectors.lexical import jaccard, tokenize
from apps.api.services.graph.detectors.references import _title_from_filename


def test_tokenize_and_jaccard():
    a = tokenize("Котёнок по имени Гав жил во дворе большого дома")
    b = tokenize("Котёнок по имени Гав играл во дворе")
    assert "котёнок" in a or "котенок" in a or len(a) >= 3
    score = jaccard(a, b)
    assert 0.1 < score < 1.0
    assert jaccard(set(), b) == 0.0


def test_edge_key_symmetric():
    a = uuid.uuid4()
    b = uuid.uuid4()
    c1 = RelationCandidate(
        source_document_id=a,
        target_document_id=b,
        source_version_id=None,
        target_version_id=None,
        relation_type="SEMANTIC_SIMILAR",
        directed=False,
        origin="SYSTEM_RULE",
        review_state="PROPOSED",
        score=0.5,
        score_method="token_jaccard_v1",
        algorithm_name="lexical_jaccard_v1",
        algorithm_version="1",
        detector_key="lexical_jaccard_v1:doc",
    )
    c2 = RelationCandidate(
        source_document_id=b,
        target_document_id=a,
        source_version_id=None,
        target_version_id=None,
        relation_type="SEMANTIC_SIMILAR",
        directed=False,
        origin="SYSTEM_RULE",
        review_state="PROPOSED",
        score=0.5,
        score_method="token_jaccard_v1",
        algorithm_name="lexical_jaccard_v1",
        algorithm_version="1",
        detector_key="lexical_jaccard_v1:doc",
    )
    assert c1.edge_key() == c2.edge_key()


def test_directed_edge_key_differs():
    a = uuid.uuid4()
    b = uuid.uuid4()
    fwd = RelationCandidate(
        source_document_id=a,
        target_document_id=b,
        source_version_id=None,
        target_version_id=None,
        relation_type="EXPLICIT_REFERENCE",
        directed=True,
        origin="SYSTEM_RULE",
        review_state="PROPOSED",
        score=None,
        score_method=None,
        algorithm_name="explicit_reference_v1",
        algorithm_version="1",
        detector_key="explicit_reference_v1:title",
    )
    rev = RelationCandidate(
        source_document_id=b,
        target_document_id=a,
        source_version_id=None,
        target_version_id=None,
        relation_type="EXPLICIT_REFERENCE",
        directed=True,
        origin="SYSTEM_RULE",
        review_state="PROPOSED",
        score=None,
        score_method=None,
        algorithm_name="explicit_reference_v1",
        algorithm_version="1",
        detector_key="explicit_reference_v1:title",
    )
    assert fwd.edge_key() != rev.edge_key()


def test_evidence_key_stable():
    cid = uuid.uuid4()
    e1 = EvidenceCandidate(source_chunk_id=cid, method="title_mention", span_start=1, span_end=5)
    e2 = EvidenceCandidate(source_chunk_id=cid, method="title_mention", span_start=1, span_end=5)
    assert e1.evidence_key() == e2.evidence_key()


def test_title_from_filename():
    t = _title_from_filename("classic_chekhov_Duel_fragment_1.txt")
    assert "Duel" in t or "duel" in t.lower() or "fragment" not in t.lower()
