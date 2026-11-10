"""Lifecycle tests: empty / replace / incremental ≈ full (offline, no DB)."""

from __future__ import annotations

from collections import Counter

from apps.api.services import graph_index as gi
from apps.api.services import graph_index_cache as gcache


def _edge_keyset(edges: list[gi.Edge]) -> set[tuple[str, str, str]]:
    out = set()
    for e in edges:
        a, b = e.source, e.target
        if a > b and e.type.startswith("work_") is False and e.type not in (
            "in_theme",
            "wrote",
            "part_of",
            "work_meta",
            "mentions_author",
            "work_mentions_author",
        ):
            a, b = b, a
        out.add((e.type, a, b))
    return out


def test_empty_corpus_assemble_ok():
    payload = gi._empty_index_payload(
        tenant_id="t",
        fingerprint="empty",
        params=gi.GraphIndexParams(),
        built_ms=1,
        mode="full",
        params_hash_value="abc",
    )
    assert payload["stats"]["documents"] == 0
    assert payload["edges"] == []
    assert payload["version"] == gi.INDEX_VERSION


def test_invalidate_clears_indexes_and_docs(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.api.services.graph_index_cache.data_root", lambda: tmp_path)
    monkeypatch.setattr("apps.api.services.storage.data_root", lambda: tmp_path)
    tenant = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    p = gi.index_path(tenant, "fp1", params=gi.GraphIndexParams())
    gi.write_index_atomic(p, {"fingerprint": "fp1", "version": gi.INDEX_VERSION, "edges": []})
    gcache.write_doc_artifact(tenant, "doc1", {"document_id": "doc1", "content_hash": "x"})
    out = gi.invalidate_tenant(tenant)
    assert out["indexes_removed"] >= 1
    assert out["doc_artifacts_removed"] >= 1
    assert not p.exists()
    assert gcache.read_doc_artifact(tenant, "doc1") is None


def test_doc_artifact_reuse_by_content_hash(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.api.services.graph_index_cache.data_root", lambda: tmp_path)
    meta = gi.parse_doc_meta(
        document_id="d1",
        filename="classic_0001_Pushkin_Пир_aaaaaaaa.txt",
    )
    raw = [("c1", 0, "Произведение русской классики\nАвтор/папка: Pushkin\nФайл: Пир.txt\n\nПир шумел.")]
    art1 = gi._build_doc_artifact(
        meta=meta, version_id="v1", raw_chunks=raw, params=gi.GraphIndexParams()
    )
    gcache.write_doc_artifact("t1", "d1", art1)
    loaded = gcache.read_doc_artifact("t1", "d1")
    assert loaded and loaded["content_hash"] == art1["content_hash"]
    # same content → same hash
    h2 = gcache.content_hash(
        version_id="v1",
        filename=meta.filename,
        chunk_texts=[t for _, _, t in raw],
    )
    assert h2 == art1["content_hash"]


def test_incremental_layers_match_full_on_same_artifacts():
    """Same artifact set → same textual edge keys (weights may float)."""
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Pushkin_Пир_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0002_Pushkin_Онегин_bbbbbbbb.txt"),
        gi.parse_doc_meta(document_id="c", filename="classic_0003_Tolstoy_Анна_cccccccc.txt"),
    ]
    gi.resolve_work_keys(docs)
    chunks = [
        ("a", "c1", "На пиру читали роман «Евгений Онегин» вслух."),
        ("b", "c2", "Онегин ехал в деревню. Татьяна писала письмо."),
        ("c", "c3", "Анна смотрела на Вронского. Левин думал о Кити."),
    ]
    params = gi.GraphIndexParams(lex_min_score=0.05, shared_min_entities=1)
    edges1 = []
    edges1.extend(gi.layer_a_edges(docs))
    edges1.extend(gi.layer_mentions_author(docs=docs, chunk_iter=chunks))
    edges1.extend(gi.layer_b_refers_to(docs=docs, chunk_iter=chunks, params=params))
    terms = {
        "a": Counter({"пир": 3, "роман": 2, "онегин": 2, "читал": 2}),
        "b": Counter({"онегин": 4, "деревн": 2, "татьяна": 2, "письм": 2}),
        "c": Counter({"анна": 3, "вронск": 2, "левин": 2, "кити": 2}),
    }
    # Force content-like terms past proper-name filter by empty case_stats
    lex1, _ = gi.layer_d_lex_similar(docs=docs, doc_term_counts=terms, params=params, case_stats={})
    edges1.extend(lex1)
    edges2 = []
    edges2.extend(gi.layer_a_edges(docs))
    edges2.extend(gi.layer_mentions_author(docs=docs, chunk_iter=chunks))
    edges2.extend(gi.layer_b_refers_to(docs=docs, chunk_iter=chunks, params=params))
    lex2, _ = gi.layer_d_lex_similar(docs=docs, doc_term_counts=terms, params=params, case_stats={})
    edges2.extend(lex2)
    assert _edge_keyset(edges1) == _edge_keyset(edges2)


def test_delete_doc_removes_from_work_edges():
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Pushkin_Пир_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0002_Tolstoy_Анна_bbbbbbbb.txt"),
    ]
    edges = [
        gi.Edge(source="a", target="b", type="refers_to", weight=2.0, provenance={"phrase": "x"}),
    ]
    work = gi.aggregate_work_edges(docs, edges)
    assert any(e.type == "work_refers_to" for e in work)
    # after "delete" only one doc remains → no work pair
    work2 = gi.aggregate_work_edges(docs[:1], edges)
    assert not any(e.type == "work_refers_to" for e in work2)


def test_lexicon_config_loads_offline():
    from apps.api.services import graph_lexicon as gl

    gl.reload_graph_lexicon()
    data = gl.load_graph_lexicon()
    assert "genre_markers" in data
    assert "play_stage_words" in data
    assert gl.frozenset_field("play_stage_words")
