"""Slice 2b: thematic vectors, style stops, author debias, knn quota."""

from __future__ import annotations

from collections import Counter

from apps.api.services import graph_lexicon as glex
from apps.api.services import graph_topic_vectors as gtv
from apps.api.services import graph_topics as gt


def setup_module() -> None:
    glex.reload_graph_lexicon()


def test_style_stopwords_include_neshto():
    stops = gtv.style_stopwords("ru")
    assert "нешто" in stops or "нешт" in stops
    assert "дескать" in stops


def test_gerund_dropped():
    assert gtv.is_ru_gerund_or_archaic("схвативши")
    assert gtv.is_ru_gerund_or_archaic("усмехаясь")
    assert not gtv.is_ru_gerund_or_archaic("война")


def test_entropy_downweights_single_author_term():
    works = {
        "w1": {"author_key": "A", "language": "ru", "collection": "classic_"},
        "w2": {"author_key": "A", "language": "ru", "collection": "classic_"},
        "w3": {"author_key": "B", "language": "ru", "collection": "classic_"},
        "w4": {"author_key": "C", "language": "ru", "collection": "classic_"},
    }
    terms = {
        "w1": Counter({"нешто": 20, "война": 5, "кавказ": 5, "толькоуа": 12}),
        "w2": Counter({"нешто": 18, "война": 4, "кавказ": 3, "толькоуа": 10}),
        "w3": Counter({"война": 6, "кавказ": 5, "отряд": 3}),
        "w4": Counter({"война": 5, "кавказ": 4, "сакля": 3}),
    }
    params = gt.load_params()
    raw, debug = gtv.build_raw_vectors(terms, works, params=params)
    assert debug["apply_entropy"] is True
    # нешто is style-stopped → absent
    assert "нешто" not in raw["w1"]
    assert "война" in raw["w1"] or "кавказ" in raw["w1"]
    # Author-private thematic stem kept but downweighted (×0.1), not dropped
    assert "толькоуа" in raw["w1"]
    assert raw["w1"]["толькоуа"] < raw["w1"].get("война", 99) * 2


def test_author_debias_leave_one_out():
    works = {
        "w1": {"author_key": "A"},
        "w2": {"author_key": "A"},
        "w3": {"author_key": "A"},
        "solo": {"author_key": "B"},
    }
    raw = {
        "w1": {"война": 1.0, "стильа": 1.0},
        "w2": {"война": 1.0, "стильа": 0.8},
        "w3": {"война": 0.2, "деревн": 1.0},
        "solo": {"техдок": 1.0},
    }
    out = gtv.author_debias(raw, works, alpha=0.6)
    # Solo author unchanged (normalized)
    assert abs(sum(v * v for v in out["solo"].values()) - 1.0) < 1e-6
    # Shared author style dim shrinks relative to distinctive dims
    assert out["w3"]["деревн"] > out["w3"].get("стильа", 0)


def test_knn_cross_author_quota():
    works = {
        f"a{i}": {"author_key": "A", "language": "ru", "collection": "c"} for i in range(4)
    }
    works.update({f"b{i}": {"author_key": "B", "language": "ru", "collection": "c"} for i in range(4)})
    vecs = {}
    for i in range(4):
        vecs[f"a{i}"] = gtv._normalize({"война": 1.0, "кавказ": 0.8, f"aonly{i}": 0.2})
        vecs[f"b{i}"] = gtv._normalize({"война": 1.0, "кавказ": 0.7, f"bonly{i}": 0.2})
    edges = gtv.knn_edges(vecs, works, k=5, cross_author_min=2, cross_author_min_sim=0.1)
    # At least some cross-author edges
    cross = 0
    for e in edges:
        wa = e["source"][5:]
        wb = e["target"][5:]
        if works[wa]["author_key"] != works[wb]["author_key"]:
            cross += 1
    assert cross >= 2


def test_cluster_graph_excludes_work_lex_from_fusion():
    docs = [
        {
            "document_id": "d1",
            "work_key": "w1",
            "title": "Набег",
            "author_key": "Tolstoy",
            "theme": "classic_",
            "collection": "classic_",
            "language": "ru",
            "doc_kind": "fiction",
            "canonical": True,
            "relation": "canonical",
        },
        {
            "document_id": "d2",
            "work_key": "w2",
            "title": "Казаки",
            "author_key": "Lermontov",
            "theme": "classic_",
            "collection": "classic_",
            "language": "ru",
            "doc_kind": "fiction",
            "canonical": True,
            "relation": "canonical",
        },
    ]
    edges = [
        {
            "source": "work:w1",
            "target": "work:w2",
            "type": "work_lex_similar",
            "weight": 1.0,
            "provenance": {"terms": ["война", "кавказ", "отряд", "сакля", "горец"]},
        }
    ]
    graph = gt.build_cluster_graph(
        {"documents": docs, "edges": edges, "version": 16, "stats": {}},
        gt.load_params(),
    )
    types = {e["type"] for e in graph["work_edges"]}
    assert "work_lex_similar" not in types
    assert "topic_knn" in types or graph["adj"]["w1"] or graph["adj"]["w2"]
