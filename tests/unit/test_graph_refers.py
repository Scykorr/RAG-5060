"""A1: strict refers_to title matching."""

from __future__ import annotations

from apps.api.services import graph_index as gi
from apps.api.services import graph_refers as gr


def _docs_with_titles():
    return [
        gi.parse_doc_meta(document_id="src", filename="essay.txt"),
        gi.parse_doc_meta(
            document_id="nakanune",
            filename="classic_0001_Turgenev_Накануне_aaaaaaaa.txt",
        ),
        gi.parse_doc_meta(
            document_id="tri_smerti",
            filename="classic_0002_Tolstoy_Три_смерти_bbbbbbbb.txt",
        ),
        gi.parse_doc_meta(
            document_id="mother",
            filename="classic_0003_Gorky_Мать_cccccccc.txt",
        ),
        gi.parse_doc_meta(
            document_id="shinel",
            filename="classic_0004_Gogol_Шинель_dddddddd.txt",
        ),
        gi.parse_doc_meta(
            document_id="twenty6",
            filename="classic_0005_Gorky_Двадцать_шесть_и_одна_eeeeeeee.txt",
        ),
    ]


def test_lowercase_nakanune_not_a_reference():
    docs = _docs_with_titles()
    chunks = [("src", "c1", "Пьер накануне того воскресенья долго думал.")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.target == "nakanune" for e in edges)


def test_genre_quoted_nakanune_is_reference():
    docs = _docs_with_titles()
    chunks = [("src", "c1", "В романе Тургенева «Накануне» герои спорят.")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    hit = [e for e in edges if e.target == "nakanune"]
    assert hit
    assert hit[0].provenance.get("match_form") in {"quoted", "genre_marker"}
    assert hit[0].provenance.get("multiword") is False  # one-word title


def test_partial_title_smerti_not_tri_smerti():
    docs = _docs_with_titles()
    chunks = [("src", "c1", "Это было до смерти Бибикова.")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.target == "tri_smerti" for e in edges)


def test_genre_quoted_tri_smerti():
    docs = _docs_with_titles()
    chunks = [("src", "c1", "В рассказе «Три смерти» Толстой пишет о конце.")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    hit = [e for e in edges if e.target == "tri_smerti"]
    assert hit
    assert hit[0].provenance.get("multiword") is True
    assert hit[0].provenance.get("match_form") in {"quoted", "genre_marker"}


def test_quoted_phrase_containing_mother_not_title():
    docs = _docs_with_titles()
    chunks = [("src", "c1", "Он хотел «обнять свою мать» и уйти.")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.target == "mother" for e in edges)


def test_genre_far_from_title_does_not_count():
    docs = _docs_with_titles()
    chunks = [
        (
            "src",
            "c1",
            "Всё это на роман похоже, думал он, глядя на мать у окна.",
        )
    ]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.target == "mother" for e in edges)


def test_lowercase_shinel_not_reference():
    docs = _docs_with_titles()
    chunks = [("src", "c1", "Он носил старую шинель всю зиму.")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.target == "shinel" for e in edges)


def test_multiword_flag_for_twenty_six():
    phrases = gr.build_title_phrases(_docs_with_titles())
    tw = [p for p in phrases if p[1] == "twenty6"]
    assert tw and tw[0][3] is True


def test_exact_quote_required_for_one_word():
    docs = _docs_with_titles()
    # capitalized mid-sentence alone is not enough for one-word
    chunks = [("src", "c1", "Он вспомнил Мать и заплакал.")]
    edges = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks)
    assert not any(e.target == "mother" for e in edges)

    chunks2 = [("src", "c2", "В повести «Мать» Горький описывает завод.")]
    edges2 = gi.layer_b_refers_to(docs=docs, chunk_iter=chunks2)
    assert any(e.target == "mother" for e in edges2)
