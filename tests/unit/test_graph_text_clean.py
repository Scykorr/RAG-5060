"""Tests for clean_chunk_for_graph (slice 1c step 1)."""

from apps.api.services.graph_text_clean import clean_chunk_for_graph


def test_strips_author_papka_file_banner():
    raw = (
        "Произведение русской классики\n"
        "Автор/папка: Bryusov\n"
        "Файл: Бемоль.txt\n"
        "\n"
        "Как только Анна Николаевна кончила пансион, ей подыскали место.\n"
    )
    clean, info = clean_chunk_for_graph(raw, chunk_ordinal=2, doc_meta={"title": "Бемоль"})
    assert "Автор/папка" not in clean
    assert "Файл:" not in clean
    assert "Произведение русской классики" not in clean
    assert "Как только Анна Николаевна" in clean
    assert info["header_stripped"] is True
    assert info["author"] == "Bryusov"
    assert info.get("file") == "Бемоль.txt"
    assert info["chars_removed"] > 0


def test_strips_nazvanie_chast_banner():
    raw = (
        "Произведение русской классической литературы\n"
        "Автор: Dostoevsky\n"
        "Название/часть: Братья Карамазовы__фрагмент_2\n"
        "\n"
        "Алеша, выслушав приказание отца, оставался в недоумении.\n"
    )
    clean, info = clean_chunk_for_graph(raw, chunk_ordinal=5)
    assert "Название/часть" not in clean
    assert "Автор:" not in clean
    assert "Алеша, выслушав" in clean
    assert info["author"] == "Dostoevsky"
    assert "Братья Карамазовы" in info.get("title", "")


def test_strips_fetch_ru_corpus_banner():
    raw = (
        "Произведение: Капитанская дочка\n"
        "Автор: Pushkin\n"
        "Источник: /data/books/Pushkin/cap.txt\n"
        "Фрагмент: 1/3\n"
        "\n"
        "Отец мой Андрей Петрович Гринев в молодости своей служил.\n"
    )
    clean, info = clean_chunk_for_graph(raw, chunk_ordinal=0)
    assert "Источник:" not in clean
    assert "Фрагмент:" not in clean
    assert "Отец мой Андрей Петрович" in clean
    assert info["author"] == "Pushkin"
    assert info.get("title") == "Капитанская дочка"


def test_strips_book_title_and_edition_on_early_chunk():
    raw = (
        "Произведение русской классики\n"
        "Автор/папка: Pushkin\n"
        "Файл: Египетские ночи.txt\n"
        "\n"
        "Александр Пушкин ЕГИПЕТСКИЕ НОЧИ ГЛАВА I\n"
        "Л.Н. Толстой. Собрание сочинений в 22 т. М.: Художественная литература\n"
        "Текст с Полного собрания сочинений Пушкина на CDROM\n"
        "\n"
        "И вот однажды зимой он сидел у себя в кабинете.\n"
    )
    clean, info = clean_chunk_for_graph(
        raw,
        chunk_ordinal=0,
        doc_meta={"author_key": "Pushkin", "title": "Египетские ночи"},
    )
    assert "ЕГИПЕТСКИЕ НОЧИ" not in clean
    assert "Собрание сочинений" not in clean
    assert "CDROM" not in clean
    assert "И вот однажды зимой" in clean
    assert info["front_matter_stripped"] is True


def test_does_not_strip_ordinary_body_text():
    body = (
        "Вронский смотрел на Анну. Зачем она так сказала? "
        "Наконец он понял, что любовь требует жертв.\n"
        "Почему бы не уехать в Москву на железной дороги?\n"
    )
    clean, info = clean_chunk_for_graph(
        body,
        chunk_ordinal=3,
        doc_meta={"author_key": "Tolstoy", "title": "Анна Каренина"},
    )
    assert clean == body.strip()
    assert info["header_stripped"] is False
    assert "Вронский" in clean
    assert "железной дороги" in clean


def test_late_chunk_keeps_incidental_caps_line():
    raw = (
        "Произведение русской классики\n"
        "Автор/папка: Chekhov\n"
        "Файл: Беглец.txt\n"
        "\n"
        "Это была длинная процедура. Сначала Пашка шел с матерью.\n"
    )
    clean, _info = clean_chunk_for_graph(
        raw,
        chunk_ordinal=4,
        doc_meta={"author_key": "Chekhov", "title": "Беглец"},
    )
    assert "Это была длинная процедура" in clean
    assert "Автор/папка" not in clean
