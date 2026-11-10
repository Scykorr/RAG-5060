"""A4 mentions_author + A5 text_header author detection."""

from __future__ import annotations

from apps.api.services import graph_authors as ga
from apps.api.services import graph_index as gi
from apps.api.services.graph_lexicon import reload_graph_lexicon


def setup_module(_mod=None):
    reload_graph_lexicon()


def test_lowercase_tolstoy_adjective_not_author():
    hits = ga.find_author_mentions("видел толстого, заплывшего жиром мужика")
    assert hits == []


def test_hyphenated_musin_pushkin_rejected():
    hits = ga.find_author_mentions("Письмо Мусин-Пушкину о библиотеке.")
    assert not any(h["author_key"] == "Pushkin" for h in hits)


def test_wrong_given_name_nekrasov_rejected():
    hits = ga.find_author_mentions("Игнатий Некрасов приехал в город.")
    assert not any(h["author_key"] == "Nekrasov" for h in hits)


def test_wrong_initials_tolstoy_rejected():
    assert not any(
        h["author_key"] == "Tolstoy"
        for h in ga.find_author_mentions("Письмо Я. Н. Толстому осталось без ответа.")
    )
    assert not any(
        h["author_key"] == "Tolstoy"
        for h in ga.find_author_mentions("Собрание Ф. И. Толстого.")
    )


def test_valid_author_mentions_with_confidence():
    high = ga.find_author_mentions("Лев Толстой писал о войне.")
    assert any(h["author_key"] == "Tolstoy" and h["confidence"] == "high" for h in high)

    init = ga.find_author_mentions("Роман Л. Толстого переиздан.")
    assert any(h["author_key"] == "Tolstoy" and h["confidence"] == "high" for h in init)

    bare = ga.find_author_mentions("Критик сравнил это с Пушкина.")
    hit = [h for h in bare if h["author_key"] == "Pushkin"]
    assert hit and hit[0]["confidence"] == "medium"


def test_commentary_zone_on_mentions_edge():
    docs = [
        gi.parse_doc_meta(document_id="src", filename="essay.txt"),
        gi.parse_doc_meta(
            document_id="p",
            filename="classic_0001_Pushkin_Пир_aaaaaaaa.txt",
        ),
    ]
    chunks = [
        (
            "src",
            "c1",
            "Основной текст без имён.\n\nПримечания\n* См. Пушкина, том 2.",
        )
    ]
    edges = gi.layer_mentions_author(docs=docs, chunk_iter=chunks)
    hit = [e for e in edges if e.target == "author:Pushkin"]
    assert hit
    assert hit[0].provenance.get("zone") == "commentary"
    assert hit[0].weight < 1.0


def test_herzen_text_header_author():
    raw = (
        "Александр Иванович Герцен\n\n"
        "ПОВРЕЖДЕННЫЙ\n\n"
        "Повесть\n\n"
        "I\n"
        "Текст повести дальше."
    )
    assert ga.detect_author_from_text_header(raw) == "Herzen"

    meta = gi.parse_doc_meta(
        document_id="h1",
        filename="classic_0099_Повреждённый_aaaaaaaa.txt",
    )
    assert meta.author_key is None
    art = gi._build_doc_artifact(
        meta=meta,
        version_id="v1",
        raw_chunks=[("c0", 0, raw)],
        params=gi.GraphIndexParams(),
    )
    assert meta.author_key == "Herzen"
    assert meta.author_source == "text_header"
    assert art["author_key"] == "Herzen"
