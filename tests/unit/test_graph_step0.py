"""Slice 2 / step 0 leftovers from 1e."""

from __future__ import annotations

from collections import Counter

from apps.api.services import graph_index as gi
from apps.api.services import graph_lang as glang
from apps.api.services import graph_refers as gr
from apps.api.services.graph_text_clean import strip_stage_directions
from apps.api.services.graph_lexicon import reload_graph_lexicon


def setup_module() -> None:
    reload_graph_lexicon()


def test_person_title_elena_genre_not_refers():
    docs = [
        gi.parse_doc_meta(document_id="src", filename="classic_0200_Gorky_Essay_aaaaaaaa.txt"),
        gi.parse_doc_meta(
            document_id="elena", filename="classic_0201_Turgenev_Елена_bbbbbbbb.txt"
        ),
    ]
    for d in docs:
        d.language = "ru"
        d.doc_kind = "fiction"
    chunks = [
        (
            "src",
            "c1",
            "В день, с которого начался наш рассказ, Елена дольше обыкновенного не отходила от окна.",
        )
    ]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.target == "elena" for e in edges)


def test_boris_godunov_capitalized_not_refers():
    docs = [
        gi.parse_doc_meta(
            document_id="src", filename="classic_0300_Tolstoy_Essay_aaaaaaaa.txt"
        ),
        gi.parse_doc_meta(
            document_id="bg", filename="classic_0301_Pushkin_Борис_Годунов_bbbbbbbb.txt"
        ),
    ]
    chunks = [
        (
            "src",
            "c1",
            "Нынче это Борис Годунов, завтра Григорий Отрепьев, нынче распутная Екатерина.",
        )
    ]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.target == "bg" for e in edges)


def test_ispoved_prefix_of_longer_chapter_not_refers():
    docs = [
        gi.parse_doc_meta(
            document_id="src", filename="classic_0400_Dostoevsky_Karamazov_aaaaaaaa.txt"
        ),
        gi.parse_doc_meta(
            document_id="isp", filename="classic_0401_Tolstoy_Исповедь_bbbbbbbb.txt"
        ),
        gi.parse_doc_meta(
            document_id="hot",
            filename="classic_0402_Dostoevsky_Исповедь_горячего_сердца_cccccccc.txt",
        ),
    ]
    chunks = [("src", "c1", "III Исповедь горячего сердца. В стихах")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.target == "isp" for e in edges)


def test_vedma_competing_name_blocks_refers():
    docs = [
        gi.parse_doc_meta(
            document_id="src", filename="classic_0500_Bryusov_Essay_aaaaaaaa.txt"
        ),
        gi.parse_doc_meta(
            document_id="vedma", filename="classic_0501_Bryusov_Ведьма_bbbbbbbb.txt"
        ),
    ]
    chunks = [
        (
            "src",
            "c1",
            'Мирандола написал диалог "Ведьма", с целью убедить образованных людей.',
        )
    ]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.target == "vedma" for e in edges)


def test_metel_ambiguous_prefers_same_author():
    docs = [
        gi.parse_doc_meta(
            document_id="src",
            filename="classic_0100_Pushkin_Романы_и_повести_aaaaaaaa.txt",
        ),
        gi.parse_doc_meta(
            document_id="mp", filename="classic_0101_Pushkin_Метель_bbbbbbbb.txt"
        ),
        gi.parse_doc_meta(
            document_id="mt", filename="classic_0102_Tolstoy_Метель_cccccccc.txt"
        ),
    ]
    for d in docs:
        d.language = "ru"
    chunks = [
        (
            "src",
            "c1",
            "Повести покойного Ивана Петровича Белкина: «Метель» и «Барышня» девицею К. И. Т.",
        )
    ]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    targets = {e.target for e in edges}
    assert "mp" in targets
    assert "mt" not in targets or all(
        (e.provenance or {}).get("confidence") == "low" for e in edges if e.target == "mt"
    )


def test_ivan_fedorovich_alone_no_cross_author_entity_edge():
    docs = [
        gi.parse_doc_meta(document_id="idiot", filename="classic_a_Dostoevsky_Идиот_aaaaaaaa.txt"),
        gi.parse_doc_meta(
            document_id="karam", filename="classic_b_Dostoevsky_Братья_Карамазовы_bbbbbbbb.txt"
        ),
    ]
    # Same author actually — use different authors to force cross-author gate
    docs[0].author_key = "Dostoevsky"
    docs[1].author_key = "Tolstoy"
    docs[0].work_key = "classic|Dostoevsky|idiot"
    docs[1].work_key = "classic|Tolstoy|karam"
    docs[0].canonical = True
    docs[1].canonical = True
    for d in docs:
        d.language = "ru"
        d.doc_kind = "fiction"
    edges = gi.layer_c_shared_entity(
        docs=docs,
        doc_entities={"idiot": ["Иван Федорович"], "karam": ["Иван Федорович"]},
    )
    assert not any({e.source, e.target} == {"idiot", "karam"} for e in edges)


def test_french_chunk_is_other_not_en():
    fr = (
        "Le prince Andrei regarda le ciel. Il dit: je ne sais pas. "
        "Dans la guerre et la paix, tout change pour lui et pour elle."
    )
    assert glang.detect_language(fr) == "other"


def test_stage_directions_stripped_from_lex_text():
    raw = "ИВАНОВ. (целует её руку) Маша, прощай.\nМАША. (уходит) До завтра."
    cleaned = strip_stage_directions(raw)
    assert "целует" not in cleaned
    assert "уходит" not in cleaned
    assert "ИВАНОВ" in cleaned or "Иванов" in cleaned.upper()


def test_person_like_title_helpers():
    assert gr.is_person_like_title("Елена") is True
    assert gr.is_person_like_title("Борис Годунов") is True
    assert gr.is_person_like_title("Накануне") is False
    assert gr.is_person_like_title("Метель") is False
