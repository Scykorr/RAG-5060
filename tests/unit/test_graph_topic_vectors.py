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


def test_proper_noun_cap_ratio_and_entity_ban():
    case = {
        "Хлестаков": {"upper": 5, "lower": 0},
        "хлестаков": {"upper": 0, "lower": 1},
        "казаки": {"upper": 1, "lower": 20},
        "батареи": {"upper": 0, "lower": 15},
        "учения": {"upper": 2, "lower": 12},
        "станица": {"upper": 1, "lower": 10},
    }
    pos = {
        "Хлестаков": {"mid": 5, "sent_start": 0},
        "хлестаков": {"mid": 0, "sent_start": 0},
        "казаки": {"mid": 1, "sent_start": 0},
        "батареи": {"mid": 0, "sent_start": 0},
        "учения": {"mid": 1, "sent_start": 1},
        "станица": {"mid": 0, "sent_start": 1},
    }
    proper, rows = gtv.detect_proper_nouns(
        case, pos, min_n=3, min_cap_ratio=0.8, entity_stems={"раскольников"}
    )
    # stems of Хлестаков / Раскольников
    assert any("хлестаков" in s or s.startswith("хлестак") for s in proper)
    assert "раскольников" in proper or any("раскольник" in s for s in proper)
    assert not any(s.startswith("казак") for s in proper)
    assert not any(s.startswith("батарей") or s.startswith("батаре") for s in proper)
    assert not any(s.startswith("учен") for s in proper)
    assert not any(s.startswith("станиц") for s in proper)
    assert rows and rows[0].get("cap_ratio") is not None or any(r.get("source") == "entity" for r in rows)


def test_proper_noun_dialogue_line_starts():
    # Character name mostly at dialogue/line starts, almost never lowercase.
    case = {"Хлестаков": {"upper": 270, "lower": 0}, "Нина": {"upper": 100, "lower": 1}}
    pos = {
        "Хлестаков": {"mid": 10, "sent_start": 260},
        "Нина": {"mid": 40, "sent_start": 60},
    }
    proper, rows = gtv.detect_proper_nouns(case, pos, min_n=3, min_cap_ratio=0.8)
    assert any(s.startswith("хлестак") for s in proper)
    assert any(s.startswith("нин") for s in proper)
    assert any(r.get("source") == "cap_dialogue" for r in rows)


def test_proper_noun_zero_weight_in_vectors():
    works = {
        "w1": {"author_key": "A", "language": "ru", "collection": "c"},
        "w2": {"author_key": "B", "language": "ru", "collection": "c"},
        "w3": {"author_key": "C", "language": "ru", "collection": "c"},
    }
    terms = {
        "w1": Counter({"хлестаков": 12, "война": 5, "казак": 4}),
        "w2": Counter({"хлестаков": 8, "война": 6, "казак": 3}),
        "w3": Counter({"война": 5, "казак": 5, "станиц": 3}),
    }
    case = {"Хлестаков": {"upper": 10, "lower": 0}, "казак": {"upper": 1, "lower": 30}}
    pos = {"Хлестаков": {"mid": 10, "sent_start": 0}, "казак": {"mid": 1, "sent_start": 0}}
    params = gt.load_params()
    raw, debug = gtv.build_raw_vectors(
        terms, works, params=params, case_stats=case, pos_stats=pos
    )
    assert debug["proper_nouns_n"] >= 1
    for wk in terms:
        assert not any(str(s).startswith("хлестак") for s in raw[wk])
        assert "казак" in raw[wk] or "война" in raw[wk]


def test_filter_config_hash_stable():
    p = gt.load_params()
    a = gtv.filter_config_hash(p)
    b = gtv.filter_config_hash(dict(p))
    assert a == b
    p2 = dict(p)
    p2["proper_noun_min_cap_ratio"] = 0.9
    assert gtv.filter_config_hash(p2) != a
