"""Slice 2: clustering graph, Louvain topics, stable ids, labels."""

from __future__ import annotations

from collections import Counter

import pytest

from apps.api.services import graph_index as gi
from apps.api.services import graph_index_cache as gcache
from apps.api.services import graph_louvain
from apps.api.services import graph_topics as gt


def _params(**overrides):
    cfg = gt.load_params()
    cfg.update(overrides)
    cfg["llm"] = overrides.get("llm", False)
    return cfg


def _doc(wk: str, title: str, author: str, *, collection: str = "classic_", lang: str = "ru", kind: str = "fiction"):
    return {
        "document_id": f"doc-{wk}",
        "work_key": wk,
        "title": title,
        "author_key": author,
        "author_ru": author,
        "theme": collection,
        "collection": collection,
        "language": lang,
        "doc_kind": kind,
        "canonical": True,
        "relation": "canonical",
        "filename": f"{collection}{author}_{title}.txt",
    }


def _lex(a: str, b: str, w: float = 1.0, terms: list[str] | None = None, weak: bool = False):
    return {
        "source": f"work:{a}",
        "target": f"work:{b}",
        "type": "work_lex_similar",
        "weight": w,
        "provenance": {"terms": terms or ["война", "кавказ", "горцы"], "weak": weak},
    }


def _entity(a: str, b: str, name: str = "RFC2119", w: float = 1.0):
    return {
        "source": f"work:{a}",
        "target": f"work:{b}",
        "type": "work_shared_entity",
        "weight": w,
        "provenance": {
            "entities": [name],
            "entity_strength": {name: "strong"},
            "entity_kinds": {name: "standard"},
        },
    }


def _clique(keys: list[str], w: float = 1.0, terms: list[str] | None = None) -> list[dict]:
    edges = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            edges.append(_lex(a, b, w, terms))
    return edges


def _index(docs: list[dict], edges: list[dict], **stats) -> dict:
    life = {"added": 0, "changed": 0, "removed": 0, "cached": len(docs)}
    return {
        "version": 16,
        "fingerprint": "fp-test",
        "params_hash": "ph",
        "documents": docs,
        "edges": edges,
        "stats": {"build_mode": "full", "lifecycle": life, **stats},
        "stale": False,
    }


def _topics_of(payload: dict, keys: list[str]) -> set[str]:
    return {payload["assignments"][k]["topic_id"] for k in keys}


def test_config_same_author_factor_is_0_6():
    cfg = gt.load_params()
    assert cfg["same_author_factor"] == 0.6


def test_collection_alias_not_a_cluster_edge():
    assert gt.collection_of({"theme": "classic_"}) == "classic_"
    assert gt.collection_of({"collection": "tech_"}) == "tech_"
    assert gt.is_collection_edge({"type": "in_theme"})
    assert gt.is_collection_edge({"type": "in_collection"})
    docs = [
        _doc("w1", "Один", "A"),
        _doc("w2", "Два", "B"),
    ]
    edges = [
        {
            "source": "theme:classic_",
            "target": "doc-w1",
            "type": "in_theme",
            "weight": 1.0,
            "provenance": {"theme": "classic_"},
        },
        {
            "source": "author:A",
            "target": "doc-w1",
            "type": "wrote",
            "weight": 1.0,
            "provenance": {},
        },
    ]
    graph = gt.build_cluster_graph(_index(docs, edges), _params())
    assert graph["work_edges"] == []
    assert gt._degree0(graph["works"], graph["adj"]) == ["w1", "w2"]


def test_louvain_deterministic_and_two_cliques():
    nodes = [f"n{i}" for i in range(12)]
    edges = []
    for i, a in enumerate(nodes[:6]):
        for b in nodes[:6][i + 1 :]:
            edges.append((a, b, 1.0))
    for i, a in enumerate(nodes[6:]):
        for b in nodes[6:][i + 1 :]:
            edges.append((a, b, 1.0))
    edges.append(("n0", "n6", 0.05))
    first = graph_louvain.louvain(nodes, edges, resolution=1.0, seed=42)
    second = graph_louvain.louvain(nodes, edges, resolution=1.0, seed=42)
    assert first == second
    assert len({first[n] for n in nodes[:6]}) == 1
    assert len({first[n] for n in nodes[6:]}) == 1
    assert first["n0"] != first["n6"]


def test_two_clusters_bridge_does_not_merge():
    a = [f"a{i}" for i in range(6)]
    b = [f"b{i}" for i in range(6)]
    docs = [_doc(k, k, "AuthorA") for k in a] + [_doc(k, k, "AuthorB") for k in b]
    edges = _clique(a, 1.0, ["север"]) + _clique(b, 1.0, ["юг"]) + [_lex("a0", "b0", 0.05, ["мост"])]
    payload = gt.build_topics_from_index(_index(docs, edges), params=_params(topic_resolution=1.0))
    topics_a = _topics_of(payload, a)
    topics_b = _topics_of(payload, b)
    assert len(topics_a) == 1
    assert len(topics_b) == 1
    assert topics_a != topics_b


def test_same_author_factor_keeps_cross_author_theme():
    a = ["a1", "a2", "a3"]
    b = ["b1", "b2", "b3"]
    docs = [_doc(k, f"Кавказ {k}", "Lermontov") for k in a] + [_doc(k, f"Кавказ {k}", "Tolstoy") for k in b]
    edges = _clique(a, 0.45, ["кавказ", "война"]) + _clique(b, 0.45, ["кавказ", "война"])
    for x in a:
        for y in b:
            edges.append(_lex(x, y, 1.0, ["кавказ", "война", "горцы"]))
    payload = gt.build_topics_from_index(
        _index(docs, edges),
        params=_params(topic_resolution=1.0, min_topic_size=3),
    )
    content = [t for t in payload["topics"] if not t.get("service")]
    assert content
    joined = next(t for t in content if set(a) & set(t["work_keys"]) or set(b) & set(t["work_keys"]))
    authors = {row["author_key"] for row in joined["authors"] if row.get("author_key")}
    assert authors == {"Lermontov", "Tolstoy"}
    assert payload["params"]["same_author_factor"] == 0.6


def test_determinism_same_ids():
    keys = [f"k{i}" for i in range(8)]
    docs = [_doc(k, f"Текст {k}", "A" if i < 4 else "B") for i, k in enumerate(keys)]
    edges = _clique(keys[:4], 1.0, ["море"]) + _clique(keys[4:], 1.0, ["степь"])
    params = _params(topic_resolution=1.0)
    index = _index(docs, edges)
    first = gt.build_topics_from_index(index, params=params)
    second = gt.build_topics_from_index(index, params=params)
    assert first["assignments"] == second["assignments"]
    assert [t["id"] for t in first["topics"]] == [t["id"] for t in second["topics"]]


def test_id_stability_drop_and_restore_ten_percent():
    groups = [[f"g{g}{i}" for i in range(10)] for g in range(4)]
    docs = [_doc(k, k, f"Auth{gi}") for gi, group in enumerate(groups) for k in group]
    edges: list[dict] = []
    for group in groups:
        edges.extend(_clique(group, 1.0, ["тема"]))
    params = _params(topic_resolution=1.0, min_topic_size=3, recluster_threshold=0.2)
    full = gt.build_topics_from_index(_index(docs, edges), params=params)
    dropped = [group[0] for group in groups]
    kept_docs = [d for d in docs if d["work_key"] not in dropped]
    kept_edges = [
        e
        for e in edges
        if e["source"][5:] not in dropped and e["target"][5:] not in dropped
    ]
    mid = gt.build_topics_from_index(_index(kept_docs, kept_edges), params=params, previous=full)
    for group in groups:
        for wk in group[1:]:
            assert mid["assignments"][wk]["topic_id"] == full["assignments"][wk]["topic_id"]
    restored = gt.build_topics_from_index(
        _index(docs, edges), params=params, previous=mid, force_mode="full"
    )
    same = 0
    for d in docs:
        wk = d["work_key"]
        if restored["assignments"][wk]["topic_id"] == full["assignments"][wk]["topic_id"]:
            same += 1
    assert same / len(docs) >= 0.9


def test_incremental_batch_joins_existing_topic():
    base = [f"c{i}" for i in range(24)]
    docs = [_doc(k, k, "Pushkin") for k in base]
    edges = _clique(base, 1.0, ["поэма"])
    params = _params(topic_resolution=1.0, recluster_threshold=0.2, assign_confidence_min=0.34)
    first = gt.build_topics_from_index(_index(docs, edges), params=params)
    old_ids = {t["id"] for t in first["topics"]}
    extra = [f"n{i}" for i in range(5)]
    docs2 = docs + [_doc(k, f"поэма {k}", "Pushkin") for k in extra]
    edges2 = list(edges)
    for k in extra:
        edges2.append(_lex(k, base[0], 1.0, ["поэма"]))
        edges2.append(_lex(k, base[1], 0.9, ["поэма"]))
    second = gt.build_topics_from_index(_index(docs2, edges2), params=params, previous=first)
    assert second["assign_mode"] == "incremental"
    assert old_ids <= {t["id"] for t in second["topics"]}
    host = first["assignments"][base[0]]["topic_id"]
    for k in extra:
        assert second["assignments"][k]["topic_id"] == host
        assert second["assignments"][k]["provenance"]["mode"] == "incremental"


def test_over_threshold_recluster_keeps_ids_and_lineage():
    base = [f"p{i}" for i in range(10)]
    docs = [_doc(k, k, "A") for k in base]
    edges = _clique(base, 1.0, ["суд"])
    params = _params(topic_resolution=1.0, recluster_threshold=0.2)
    first = gt.build_topics_from_index(_index(docs, edges), params=params)
    extra = [f"q{i}" for i in range(8)]
    docs2 = docs + [_doc(k, k, "B") for k in extra]
    edges2 = edges + _clique(extra, 1.0, ["море"])
    second = gt.build_topics_from_index(_index(docs2, edges2), params=params, previous=first)
    assert second["assign_mode"] == "full"
    assert second["lineage"]["trigger"] == "threshold"
    assert second["lineage"]["matched"] or second["lineage"]["new"]
    old = first["assignments"][base[0]]["topic_id"]
    assert second["assignments"][base[0]]["topic_id"] == old
    assert any(row.get("id") == old for row in second["lineage"]["matched"]) or old in {
        t["id"] for t in second["topics"]
    }


def test_reset_clears_topics_and_ids_start_over(tmp_path, monkeypatch):
    monkeypatch.setattr(gcache, "data_root", lambda: tmp_path)
    keys = [f"r{i}" for i in range(4)]
    docs = [_doc(k, k, "A") for k in keys]
    edges = _clique(keys, 1.0)
    index = _index(docs, edges)
    tenant = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    first = gt.build_after_index(tenant, index, params=_params(topic_resolution=1.0), force=True)
    assert gt.load_topics(tenant)["generation"] == 1
    # A label that lived only in the topics file must disappear with reset.
    assert first["topics"]
    cleared = gi.invalidate_tenant(tenant)
    assert cleared["topics_removed"] >= 1
    assert gt.load_topics(tenant) is None
    second = gt.build_after_index(tenant, index, params=_params(topic_resolution=1.0), force=True)
    assert second["generation"] == 1
    assert second["lineage"]["trigger"] == "initial"
    assert second["lineage"]["matched"] == []


def test_ollama_down_falls_back_to_terms():
    keys = [f"o{i}" for i in range(4)]
    docs = [_doc(k, f"Хаджи Мурат {k}", "Tolstoy") for k in keys]
    edges = _clique(keys, 1.0, ["кавказ", "война", "горцы"])
    params = _params(topic_resolution=1.0, llm=True)

    def _down(_prompt: str, _model: str) -> str:
        raise ConnectionError("ollama down")

    payload = gt.build_topics_from_index(
        _index(docs, edges), params=params, llm_generate=_down
    )
    content = [t for t in payload["topics"] if not t.get("service")]
    assert content
    assert all(t["label_source"] == "terms" for t in content)
    assert all(t.get("label") for t in content)


def test_override_label_survives_rebuild():
    keys = [f"v{i}" for i in range(4)]
    docs = [_doc(k, k, "A") for k in keys]
    edges = _clique(keys, 1.0, ["кавказ", "война"])
    params = _params(topic_resolution=1.0)
    index = _index(docs, edges)
    first = gt.build_topics_from_index(index, params=params)
    tid = first["assignments"][keys[0]]["topic_id"]
    overrides = {
        "topics": {tid: {"label": "Кавказ и война", "pin": True}},
        "themes": {},
        "merges": [],
        "splits": [],
    }
    second = gt.build_topics_from_index(
        index, params=params, previous=first, overrides=overrides, force_mode="full"
    )
    row = next(t for t in second["topics"] if t["id"] == tid)
    assert row["label"] == "Кавказ и война"
    assert row["label_source"] == "override"


def test_weak_edges_are_half_weight():
    docs = [_doc("w1", "A", "A"), _doc("w2", "B", "B"), _doc("w3", "C", "C")]
    edges = [_lex("w1", "w2", 1.0, weak=False), _lex("w1", "w3", 1.0, weak=True)]
    graph = gt.build_cluster_graph(_index(docs, edges), _params())
    by_pair = {}
    for edge in graph["work_edges"]:
        pair = tuple(sorted((edge["source"], edge["target"])))
        by_pair[pair] = edge
    strong = by_pair[("work:w1", "work:w2")]
    weak = by_pair[("work:w1", "work:w3")]
    assert weak["weak"] is True
    assert strong["weight"] == pytest.approx(weak["weight"] * 2, rel=1e-3)


def test_mixed_corpus_tech_theme_and_identifier_cross_lang():
    ru = [f"ru{i}" for i in range(4)]
    en = [f"en{i}" for i in range(4)]
    cross = ["c0", "c1", "c2"]
    docs = (
        [_doc(k, f"Война {k}", "Tolstoy", lang="ru", kind="fiction") for k in ru]
        + [_doc(k, f"TLS {k}", "IETF", collection="tech_", lang="en", kind="technical") for k in en]
        + [
            _doc("c0", "RFC notes", "IETF", collection="tech_", lang="en", kind="technical"),
            _doc("c1", "Стандарт", "Editor", lang="ru", kind="nonfiction"),
            _doc("c2", "RFC guide", "IETF", collection="tech_", lang="en", kind="technical"),
        ]
    )
    edges = (
        _clique(ru, 1.0, ["война", "полк"])
        + _clique(en, 1.0, ["protocol", "cipher"])
        + [_entity("c0", "c1"), _entity("c1", "c2"), _entity("c0", "c2")]
    )
    payload = gt.build_topics_from_index(
        _index(docs, edges),
        params=_params(topic_resolution=1.0, theme_resolution=1.0, min_topic_size=3),
    )
    ru_theme = payload["assignments"][ru[0]]["theme_id"]
    en_theme = payload["assignments"][en[0]]["theme_id"]
    assert ru_theme != en_theme
    cross_topic = payload["assignments"][cross[0]]["topic_id"]
    row = next(t for t in payload["topics"] if t["id"] == cross_topic)
    langs = set(row["languages"])
    assert "ru" in langs and "en" in langs
    types = {p["type"] for p in row["provenance"]}
    assert "work_shared_entity" in types
    assert "work_lex_similar" not in types


def test_unthemed_service_bucket():
    docs = [_doc("solo", "Одинокий", "Nobody")]
    payload = gt.build_topics_from_index(_index(docs, []), params=_params())
    topics = payload["topics"]
    assert len(topics) == 1
    assert topics[0]["label"] == "Без темы"
    assert topics[0]["service"] == "unthemed"
    assert payload["stats"]["unthemed_share"] == 1.0


def test_label_terms_prefer_links_over_character_names():
    works = {"a": {}, "b": {}}
    provenance = {"a": Counter({"войн": 2, "кавказ": 1}), "b": Counter({"войн": 1})}
    artifacts = {
        "a": Counter({"левин": 400, "войн": 3, "кавказ": 2}),
        "b": Counter({"вронский": 200, "войн": 4}),
    }
    merged = gt._merge_label_terms(works, provenance, artifacts, {"левин", "вронский"})
    assert "левин" not in merged["a"]
    assert "вронский" not in merged["b"]
    assert merged["a"]["войн"] > merged["a"]["кавказ"]
    # One shared edge-term outranks a capped raw count of an unrelated stem.
    assert merged["a"]["войн"] > 6
