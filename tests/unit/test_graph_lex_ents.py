"""A2 lex name exclusion + A3 entity lemma/strength tests."""

from __future__ import annotations

from collections import Counter

from apps.api.services import graph_index as gi
from apps.api.services.text_entities import norm_ru


def test_arkady_stems_excluded_from_lex():
    case = {
        "аркадий": {"upper": 20, "lower": 1},
        "аркаша": {"upper": 8, "lower": 0},
        "аркадия": {"upper": 6, "lower": 0},
    }
    pos = {
        "аркадий": {"sent_start": 2, "mid": 18},
        "аркаша": {"sent_start": 0, "mid": 8},
        "аркадия": {"sent_start": 1, "mid": 5},
    }
    stems, bases = gi.collect_lex_name_exclusions(case, pos)
    assert any(s.startswith("аркад") or s.startswith("аркаш") for s in stems)
    assert "арка" in bases or "аркад"[:4] in bases
    assert gi.is_lex_proper_token(
        "аркад", case, name_stems=stems, name_bases=bases, pos_stats=pos
    )
    assert gi.is_lex_proper_token(
        "аркаш", case, name_stems=stems, name_bases=bases, pos_stats=pos
    )


def test_slaboe_serdtse_ottsy_not_linked_by_arkady():
    docs = [
        gi.parse_doc_meta(
            document_id="a",
            filename="classic_0001_Dostoevsky_Слабое_сердце_aaaaaaaa.txt",
        ),
        gi.parse_doc_meta(
            document_id="b",
            filename="classic_0002_Turgenev_Отцы_и_дети_bbbbbbbb.txt",
        ),
    ]
    terms = {
        "a": Counter({"аркад": 8, "сердц": 5, "вася": 4, "служб": 3}),
        "b": Counter({"аркад": 9, "базар": 5, "нигил": 4, "отц": 3}),
    }
    case = {
        "аркадий": {"upper": 30, "lower": 1},
        "аркаша": {"upper": 10, "lower": 0},
    }
    pos = {
        "аркадий": {"sent_start": 3, "mid": 27},
        "аркаша": {"sent_start": 1, "mid": 9},
    }
    edges, _ = gi.layer_d_lex_similar(
        docs=docs,
        doc_term_counts=terms,
        params=gi.GraphIndexParams(lex_min_score=0.01),
        case_stats=case,
        pos_stats=pos,
    )
    assert edges == []


def test_anna_forms_one_entity_key():
    assert gi._entity_key("Анна") == gi._entity_key("Анной")
    assert gi._entity_key("Анна") == gi._entity_key("Анне")
    assert gi._entity_key("Бог") == gi._entity_key("Богом")


def test_single_weak_name_no_shared_edge():
    docs = [
        gi.parse_doc_meta(
            document_id="a",
            filename="classic_0001_Tolstoy_Анна_Каренина_aaaaaaaa.txt",
        ),
        gi.parse_doc_meta(
            document_id="b",
            filename="classic_0002_Gorky_На_дне_bbbbbbbb.txt",
        ),
    ]
    # only weak single name Анна/Анной — no strong → no edge
    edges = gi.layer_c_shared_entity(
        docs=docs,
        doc_entities={"a": ["Анна", "Анной"], "b": ["Анна"]},
        pos_stats={
            norm_ru("Анна"): {"sent_start": 1, "mid": 20},
            norm_ru("Анной"): {"sent_start": 0, "mid": 8},
        },
        case_stats={
            norm_ru("Анна"): {"upper": 21, "lower": 0},
            norm_ru("Анной"): {"upper": 8, "lower": 0},
        },
        params=gi.GraphIndexParams(shared_min_entities=1),
    )
    assert edges == []


def test_strong_surname_creates_edge():
    docs = [
        gi.parse_doc_meta(
            document_id="a",
            filename="classic_0001_Tolstoy_Анна_aaaaaaaa.txt",
        ),
        gi.parse_doc_meta(
            document_id="b",
            filename="classic_0002_Dostoevsky_Бесы_bbbbbbbb.txt",
        ),
    ]
    edges = gi.layer_c_shared_entity(
        docs=docs,
        doc_entities={
            "a": ["Вронский", "Кити"],
            "b": ["Вронский", "Смердяков"],
        },
        pos_stats={
            norm_ru("Вронский"): {"sent_start": 1, "mid": 15},
            norm_ru("Кити"): {"sent_start": 1, "mid": 10},
            norm_ru("Смердяков"): {"sent_start": 1, "mid": 12},
        },
        params=gi.GraphIndexParams(shared_min_entities=1),
    )
    assert edges
    labels = " ".join(edges[0].provenance.get("entities") or []).lower()
    assert "вронский" in labels


def test_interjections_and_pronouns_dropped():
    assert gi.is_noise_entity_surface("Господи", author_keys=set())
    assert gi.is_noise_entity_surface("Извините", author_keys=set())
    assert gi.is_noise_entity_surface("Какое-то", author_keys=set())


def test_anna_annoy_count_as_one_in_common():
    """Анна + Анной in one doc should be one lemma, not two shared."""
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Tolstoy_X_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0002_Gorky_Y_bbbbbbbb.txt"),
    ]
    # Force a strong entity too so an edge can form; Анна forms must collapse
    edges = gi.layer_c_shared_entity(
        docs=docs,
        doc_entities={
            "a": ["Анна", "Анной", "Иван Федорович"],
            "b": ["Анна", "Иван Федорович"],
        },
        pos_stats={
            norm_ru("Анна"): {"sent_start": 1, "mid": 20},
            norm_ru("Анной"): {"sent_start": 0, "mid": 10},
            "иван": {"sent_start": 0, "mid": 5},
        },
        params=gi.GraphIndexParams(shared_min_entities=1),
    )
    assert edges
    ents = edges[0].provenance.get("entities") or []
    # at most one Анна-label after lemma fold + display dedupe
    anna_labels = [e for e in ents if "анна" in norm_ru(e) and "иван" not in norm_ru(e)]
    assert len(anna_labels) <= 1
