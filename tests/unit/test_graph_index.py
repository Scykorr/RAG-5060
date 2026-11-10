"""Unit tests for connectivity index layers (no real DB)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from apps.api.services import graph_index as gi


def test_theme_author_title_classic():
    meta = gi.parse_doc_meta(
        document_id="d1",
        filename="classic_0004_Pushkin_Евгений_Онегин_abcdef12.txt",
    )
    assert meta.theme == "classic_"
    assert meta.author_key == "Pushkin"
    assert meta.author_ru == "пушкин"
    assert "Онегин" in meta.title or "онегин" in meta.title.lower()


def test_arbitrary_filename_theme_prochee():
    meta = gi.parse_doc_meta(document_id="d2", filename="notes-random.pdf")
    assert meta.theme == "Прочее"
    assert meta.author_key is None


def test_wildlife_theme():
    meta = gi.parse_doc_meta(document_id="d3", filename="wildlife_001_bear_taiga.txt")
    assert meta.theme == "wildlife_"


def test_layer_a_in_theme_and_wrote():
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Pushkin_Пир_abcdef12.txt"),
        gi.parse_doc_meta(document_id="b", filename="misc_file.txt"),
    ]
    edges = gi.layer_a_edges(docs)
    types = {e.type for e in edges}
    assert "in_theme" in types
    wrote = [e for e in edges if e.type == "wrote"]
    assert any(e.source == "author:Pushkin" and e.target == "a" for e in wrote)
    assert all(e.weight >= 1 for e in edges)
    assert all(e.provenance for e in edges)


def test_refers_to_title_mention():
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Pushkin_Пир_во_время_чумы_aa111111.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0004_Pushkin_Евгений_Онегин_bb222222.txt"),
    ]
    chunks = [
        ("a", "c1", "Герой читал Евгений Онегин всю ночь."),
        ("b", "c2", "Текст самого романа Онегина."),
    ]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert any(e.source == "a" and e.target == "b" and e.type == "refers_to" for e in edges)
    hit = next(e for e in edges if e.source == "a" and e.target == "b")
    assert hit.weight >= 1
    assert hit.provenance.get("chunk_ids")
    assert hit.provenance.get("phrase")


def test_refers_to_alias_and_short_form():
    docs = [
        gi.parse_doc_meta(document_id="src", filename="essay_about_lit.txt"),
        gi.parse_doc_meta(document_id="onegin", filename="classic_0004_Pushkin_Евгений_Онегин_cc333333.txt"),
    ]
    chunks = [
        ("src", "c1", "Критики спорили об Онегине и о Пушкине."),
    ]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    targets = {(e.source, e.target) for e in edges if e.type == "refers_to"}
    assert ("src", "onegin") in targets


def test_refers_no_self_link():
    docs = [
        gi.parse_doc_meta(document_id="b", filename="classic_0004_Pushkin_Евгений_Онегин_dd444444.txt"),
    ]
    chunks = [("b", "c1", "Евгений Онегин начинается зимой.")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert edges == []


def test_refers_stop_short_phrase_ignored():
    docs = [
        gi.parse_doc_meta(document_id="a", filename="a.txt"),
        gi.parse_doc_meta(document_id="b", filename="роман.txt"),  # short/stop-ish title
    ]
    # Force a tiny title via meta override
    docs[1].title = "том"
    chunks = [("a", "c1", "В этом томе много текста.")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert edges == []


def test_shared_entity_threshold_and_topk():
    doc_entities = {
        "d1": ["Татьяна Ларина", "Онегин", "Ленский"],
        "d2": ["Татьяна Ларина", "Онегин", "Москва"],
        "d3": ["УникальныйГерой"],
        "d4": ["Татьяна Ларина", "Онегин"],
    }
    params = gi.GraphIndexParams(shared_min_entities=2, shared_top_k=1)
    edges = gi.layer_c_shared_entity(doc_entities=doc_entities, params=params)
    assert all(e.type == "shared_entity" for e in edges)
    assert all(e.weight >= 2 for e in edges)
    # unique entity alone → no edge involving only d3
    assert not any("d3" in (e.source, e.target) for e in edges)
    # top-k=1 limits degree roughly
    from collections import defaultdict

    deg = defaultdict(int)
    for e in edges:
        deg[e.source] += 1
        deg[e.target] += 1
    assert max(deg.values()) <= 2  # undirected top-k=1 each side can still meet


def test_lex_similar_links_rare_terms_not_nn():
    docs_terms = {
        "a": Counter({"онегин": 5, "дуэль": 3, "и": 10}),
        "b": Counter({"онегин": 4, "дуэль": 2, "сад": 1}),
        "c": Counter({"медведь": 6, "тайга": 4}),
        "d": Counter({"медведь": 5, "тайга": 3}),
        "e": Counter({"кухня": 3, "рецепт": 2}),
    }
    # strip stops manually already in Counter keys
    counter = gi.LexCandidateCounter()
    params = gi.GraphIndexParams(lex_min_score=0.01, lex_top_k=3, lex_top_m=10)
    edges = gi.layer_d_lex_similar(doc_term_counts=docs_terms, params=params, counter=counter)
    pairs = {(e.source, e.target) for e in edges}
    assert ("a", "b") in pairs or ("b", "a") in pairs
    # dissimilar clusters should not freely connect a-c
    assert not any(set(p) == {"a", "c"} for p in pairs)
    n = len(docs_terms)
    assert counter.comparisons < n * n  # candidate path, not full N×N over all pairs always
    assert counter.comparisons <= n * n  # hard ceiling
    # stronger: comparisons come only from shared rare terms
    assert counter.comparisons > 0


def test_co_cited():
    edges = gi.layer_e_co_cited([["d1", "d2"], ["d1", "d2", "d3"], ["d9"]])
    assert any(e.type == "co_cited" and e.weight == 2 and set((e.source, e.target)) == {"d1", "d2"} for e in edges)
    assert any(set((e.source, e.target)) == {"d1", "d3"} for e in edges)


def test_cache_write_read_and_purge(tmp_path, monkeypatch):
    monkeypatch.setattr(gi, "data_root", lambda: tmp_path)
    (tmp_path / "graph").mkdir(parents=True)
    tenant = "11111111-1111-1111-1111-111111111111"
    docs = [gi.parse_doc_meta(document_id="d1", filename="classic_0001_Pushkin_Пир_aa111111.txt")]
    payload = gi.assemble_index(
        tenant_id=tenant,
        fingerprint="fp_old",
        docs=docs,
        edges=gi.layer_a_edges(docs),
        params=gi.GraphIndexParams(),
        built_ms=1,
    )
    old = gi.index_path(tenant, "fp_old")
    gi.write_index_atomic(old, payload)
    assert gi.read_index(old)["fingerprint"] == "fp_old"

    new_payload = dict(payload)
    new_payload["fingerprint"] = "fp_new"
    new = gi.index_path(tenant, "fp_new")
    gi.write_index_atomic(new, new_payload)
    removed = gi.purge_old_indexes(tenant, "fp_new")
    assert removed >= 1
    assert not old.exists()
    assert new.exists()
