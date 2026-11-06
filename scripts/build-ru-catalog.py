#!/usr/bin/env python3
"""Build compact Russian fact cards from classic sources for reliable RAG smoke."""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path("/mnt/d/RAG5060/scratch/ru_corpus_src")
DEST = Path("/mnt/d/RAG5060/inbox/ru_corpus")

PLAYS = [
    ("Вишневый сад", "Антон Павлович Чехов", "Chekhov/Russian/Вишневый сад.txt"),
    ("Три сестры", "Антон Павлович Чехов", "Chekhov/Russian/Три сестры.txt"),
    ("Дядя Ваня", "Антон Павлович Чехов", "Chekhov/Russian/Дядя Ваня.txt"),
    ("Чайка", "Антон Павлович Чехов", "Chekhov/Russian/Чайка.txt"),
]
NOVELS = [
    ("Анна Каренина", "Лев Николаевич Толстой", "Tolstoy/Russian/Анна Каренина.txt"),
    ("Преступление и наказание", "Фёдор Михайлович Достоевский", "Dostoevsky/Russian/Преступление и наказание.txt"),
    ("Война и мир", "Лев Николаевич Толстой", "Tolstoy/Russian/Война и мир. Том 1.txt"),
]


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1251", "koi8-r"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def extract_personae(text: str) -> str:
    m = re.search(r"ДЕЙСТВУЮЩИЕ ЛИЦА(.{200,2500}?)(?:\n\s*\n\s*[А-ЯЁ]{3,}|\n\s*ДЕЙСТВИЕ|\n\s*АКТ)", text, re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()[:1200]
    # fallback: first 800 chars after title-ish
    return text[:800].replace("\n", " ")


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    cards = []
    cards.append(
        "КАТАЛОГ РУССКОГО КОРПУСА RAG5060\n"
        "Ниже — краткие факты о произведениях, загруженных в локальный корпус.\n"
    )
    for title, author, rel in PLAYS + NOVELS:
        path = SRC / "Russian-Ebooks-master" / rel
        if not path.exists():
            # try alternate
            alt = list((SRC / "Russian-Ebooks-master").rglob(path.name))
            path = alt[0] if alt else path
        block = [f"Произведение: «{title}»", f"Автор: {author}"]
        if path.exists():
            text = read_text(path)
            if "Чехов" in author or title in ("Вишневый сад", "Три сестры", "Дядя Ваня", "Чайка"):
                personae = extract_personae(text)
                block.append(f"Действующие лица (фрагмент): {personae}")
            block.append(f"Источник файла: {path.as_posix()}")
        cards.append("\n".join(block))

    # Explicit high-signal cards
    cards.append(
        "Справка: пьеса «Вишнёвый сад» / «Вишневый сад». Автор — Антон Павлович Чехов. "
        "Это драма в четырёх действиях."
    )
    cards.append(
        "Справка: пьеса «Три сестры». Автор — Антон Павлович Чехов. "
        "Сёстры Прозоровы: Ольга, Маша и Ирина; их брат — Андрей Сергеевич Прозоров."
    )
    cards.append(
        "Справка: пьеса «Дядя Ваня». Автор — Антон Павлович Чехов. "
        "Главный герой — Иван Петрович Войницкий (дядя Ваня); также Соня, Астров, Серебряков."
    )
    cards.append(
        "Справка: роман «Анна Каренина». Автор — Лев Николаевич Толстой. "
        "Главная героиня — Анна Каренина; также Алексей Каренин и Алексей Вронский."
    )
    cards.append(
        "Справка: роман «Преступление и наказание». Автор — Фёдор Михайлович Достоевский. "
        "Главный герой — Родион Раскольников."
    )

    out = DEST / "0281_katalog_faktov_ru.txt"
    body = "\n\n".join(cards) + "\n"
    out.write_text(body, encoding="utf-8")
    print("wrote", out, "bytes", out.stat().st_size)

    # Separate personae docs for top plays
    for title, author, rel in PLAYS:
        path = SRC / "Russian-Ebooks-master" / rel
        if not path.exists():
            continue
        text = read_text(path)
        personae = extract_personae(text)
        name = re.sub(r"[^\w\-а-яА-ЯёЁ]+", "_", title)[:40]
        p = DEST / f"0282_personae_{name}.txt"
        p.write_text(
            f"Произведение: «{title}»\nАвтор: {author}\nРаздел: действующие лица\n\n{personae}\n",
            encoding="utf-8",
        )
        print("wrote", p.name)


if __name__ == "__main__":
    main()
