"""1e E3–E8 focused unit tests."""

from __future__ import annotations

from collections import Counter

from apps.api.services import graph_authors as gauth
from apps.api.services import graph_index as gi
from apps.api.services import graph_index_cache as gcache
from apps.api.services import graph_overrides as govr
from apps.api.services import graph_refers as grefer


def test_co_cited_drops_long_and_alphabetical_lists():
    edges, dropped = gi.layer_e_co_cited(
        [
            {"ids": [f"d{i}" for i in range(10)], "labels": [f"a{i}" for i in range(10)], "is_eval": False},
            {
                "ids": ["z", "y", "x", "w"],
                "labels": ["Агафья", "Анна на шее", "Аптекарша", "Ариадна"],
                "is_eval": False,
            },
            {"ids": ["a", "b"], "labels": ["B", "A"], "user_text": "сравни двух авторов", "is_eval": False},
            {"ids": ["c", "d"], "labels": ["X", "Y"], "user_text": "перечисли рассказы Чехова", "is_eval": False},
        ],
        max_sources=6,
        weight_scale=0.5,
    )
    assert dropped["too_many"] >= 1
    assert dropped["alphabetical"] >= 1
    assert dropped["list_query"] >= 1
    assert dropped["accepted"] >= 1
    assert edges and all(e.weight <= 1.0 for e in edges)


def test_shared_entity_requires_two_strong_across_authors():
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Dostoevsky_Братья_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0002_Tolstoy_Казаки_bbbbbbbb.txt"),
        gi.parse_doc_meta(document_id="c", filename="classic_0003_Dostoevsky_Идиот_cccccccc.txt"),
    ]
    gi.resolve_work_keys(docs)
    ents = {
        "a": ["Иван Федорович", "Смердяков"],
        "b": ["Иван Федорович"],
        "c": ["Иван Федорович", "Смердяков"],
    }
    edges = gi.layer_c_shared_entity(docs=docs, doc_entities=ents, params=gi.GraphIndexParams())
    pairs = {(e.source, e.target) for e in edges} | {(e.target, e.source) for e in edges}
    # cross-author Ivan alone must not link a-b
    assert ("a", "b") not in pairs


def test_work_mentions_author_weight_positive():
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Tolstoy_Поликушка_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0002_Chekhov_Степь_bbbbbbbb.txt"),
    ]
    gi.resolve_work_keys(docs)
    doc_edges = [
        gi.Edge(
            source="a",
            target="author:Chekhov",
            type="mentions_author",
            weight=0.7,
            provenance={"chunk_ids": ["c1"], "confidence": "medium"},
        )
    ]
    work = gi.aggregate_work_edges(docs, doc_edges)
    wma = [e for e in work if e.type == "work_mentions_author"]
    assert wma and wma[0].weight > 0
    assert wma[0].provenance.get("chunk_mentions", 0) >= 1


def test_gorky_ambiguous_needs_name():
    assert gauth.find_author_mentions("Горький народ. И жить стало.") == []
    hits = gauth.find_author_mentions("М. Горький писал о босяках.")
    assert any(h["author_key"] == "Gorky" for h in hits)


def test_elena_refers_requires_quotes():
    docs = [
        gi.parse_doc_meta(document_id="src", filename="classic_0001_Herzen_Кто_виноват_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="tgt", filename="classic_0002_Turgenev_Накануне_bbbbbbbb.txt"),
        gi.parse_doc_meta(document_id="elena", filename="classic_0003_Goncharov_Елена_cccccccc.txt"),
    ]
    # Goncharov may not be in author table — still title exists
    docs[2].author_key = "Goncharov"
    docs[2].work_key = "classic|Goncharov|елена"
    chunks = [
        ("src", "c1", "Елена дольше обыкновенного сидела у окна и думала."),
        ("src", "c2", "Он читал роман «Елена» вслух всему дому."),
    ]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks, params=gi.GraphIndexParams())
    to_elena = [e for e in edges if e.target == "elena"]
    assert not any("Елена дольше" in (e.provenance or {}).get("context", "") for e in to_elena)
    assert any((e.provenance or {}).get("match_form") == "quoted" for e in to_elena) or to_elena == []


def test_question_title_quoted_only():
    form = grefer.classify_hit(
        "Кто виноват в этом деле, спросил он.",
        {"start": 0, "end": 11, "capitalized": True, "multiword": True, "match_form": "capitalized"},
        author_key="Herzen",
        same_author=False,
        frequent_title=False,
        question_title=True,
    )
    assert form is None
    form_q = grefer.classify_hit(
        "роман «Кто виноват» читали все",
        {"start": 6, "end": 18, "capitalized": True, "multiword": True, "match_form": "quoted"},
        author_key="Herzen",
        same_author=False,
        frequent_title=False,
        question_title=True,
    )
    assert form_q == "quoted"


def test_noop_returns_fast(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.api.services.graph_index_cache.data_root", lambda: tmp_path)
    tenant = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    params = gi.GraphIndexParams()
    ph = gcache.params_hash(params.to_dict())
    path = gi.index_path(tenant, "fp1", params_hash_value=ph)
    payload = {
        "fingerprint": "fp1",
        "version": gi.INDEX_VERSION,
        "params_hash": ph,
        "documents": [],
        "edges": [],
        "stats": {"documents": 0, "lifecycle": {"added": 0, "changed": 0, "removed": 0, "cached": 0}},
    }
    gi.write_index_atomic(path, payload)
    gcache.write_current_pointer(
        tenant,
        {"index_file": path.name, "fingerprint": "fp1", "params_hash": ph, "stale": False, "version": gi.INDEX_VERSION},
    )

    class FakeDB:
        pass

    monkeypatch.setattr(gi, "corpus_fingerprint", lambda db, tid: "fp1")
    out = gi.build_index_for_tenant(FakeDB(), tenant, params=params, force=True)
    assert (out.get("stats") or {}).get("build_mode") == "noop"
    assert int(out.get("built_ms") or 99) < 2000


def test_overrides_apply_author(tmp_path, monkeypatch):
    ov = tmp_path / "graph_overrides.json"
    ov.write_text(
        '{"authors_by_filename_prefix": {"classic_0366": "Chekhov"}, "authors_by_work_key": {}, "merge_works": []}',
        encoding="utf-8",
    )
    monkeypatch.setattr(govr, "_DEFAULT_PATH", ov)
    govr.reload_overrides()
    docs = [
        gi.parse_doc_meta(
            document_id="x",
            filename="classic_0366_eb_Russian_Лебединая_песня_aaaaaaaa.txt",
        )
    ]
    gi.resolve_work_keys(docs)
    assert docs[0].author_key is None
    applied = govr.apply_overrides(docs)
    assert applied and docs[0].author_key == "Chekhov"
    assert docs[0].author_source == "override"
