"""Local query intent analysis for arbitrary RU questions (offline, no cloud).

Examples:
- «что можно сказать о творчестве Пушкина?» → author_overview
- «Сколько произведений Чехова в базе? выведи названия» → catalog
- «Краткое изложение рассказа «Дуэль»» → summarize
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# RU surface forms → Latin author token used in classic_* filenames
AUTHOR_ALIASES: dict[str, str] = {
    "пушкин": "Pushkin",
    "пушкина": "Pushkin",
    "пушкину": "Pushkin",
    "пушкиным": "Pushkin",
    "пушкине": "Pushkin",
    "чехов": "Chekhov",
    "чехова": "Chekhov",
    "чехову": "Chekhov",
    "чеховым": "Chekhov",
    "чехове": "Chekhov",
    "толстой": "Tolstoy",
    "толстого": "Tolstoy",
    "толстому": "Tolstoy",
    "толстым": "Tolstoy",
    "достоевский": "Dostoevsky",
    "достоевского": "Dostoevsky",
    "достоевскому": "Dostoevsky",
    "достоевским": "Dostoevsky",
    "гоголь": "Gogol",
    "гоголя": "Gogol",
    "гоголю": "Gogol",
    "лермонтов": "Lermontov",
    "лермонтова": "Lermontov",
    "тургенев": "Turgenev",
    "тургенева": "Turgenev",
    "чеховъ": "Chekhov",
    "некрасов": "Nekrasov",
    "некрасова": "Nekrasov",
    "блок": "Blok",
    "блока": "Blok",
    "брюсов": "Bryusov",
    "брюсова": "Bryusov",
    "бунин": "Bunin",
    "бунина": "Bunin",
    "островский": "Ostrovsky",
    "островского": "Ostrovsky",
    "салтыков": "Saltykov",
    "щердин": "Shchedrin",
    "щедрин": "Shchedrin",
    "гончаров": "Goncharov",
    "гончарова": "Goncharov",
    "куприн": "Kuprin",
    "куприна": "Kuprin",
    "горький": "Gorky",
    "горького": "Gorky",
    "есенин": "Esenin",
    "есенина": "Esenin",
    "маяковский": "Mayakovsky",
    "маяковского": "Mayakovsky",
    "тютчев": "Tyutchev",
    "тютчева": "Tyutchev",
    "фет": "Fet",
    "фета": "Fet",
    "крылов": "Krylov",
    "крылова": "Krylov",
    "грибоедов": "Griboedov",
    "грибоедова": "Griboedov",
    "фонвизин": "Fonvizin",
    "фонвизина": "Fonvizin",
}

_CATALOG_RE = re.compile(
    r"(сколько|перечисл|список|вывед|назван|каталог|какие\s+произведен|какие\s+рассказ|"
    r"какие\s+книг|что\s+есть\s+в\s+баз|в\s+базе|в\s+хран|индекс)",
    re.I,
)
_OVERVIEW_RE = re.compile(
    r"(творчеств|о\s+писател|что\s+(можно\s+)?сказать|расскажи\s+о|"
    r"характеристик|стиль|особенност|биограф|кто\s+такой|о\s+автор)",
    re.I,
)
_SUMMARY_RE = re.compile(
    r"(изложен|кратк(ое|ий)\s+(содержан|пересказ|обзор)|саммари|summary|"
    r"о\s+ч[её]м\s+(рассказ|повесть|роман|произведен)|перескаж|суммар)",
    re.I,
)


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


@dataclass
class QueryIntent:
    intent: str  # catalog | author_overview | summarize | extractive | general | cloze
    author_ru: str | None = None
    author_key: str | None = None  # Latin token in filename
    title: str | None = None
    want_titles: bool = False
    want_count: bool = False
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "author_ru": self.author_ru,
            "author_key": self.author_key,
            "title": self.title,
            "want_titles": self.want_titles,
            "want_count": self.want_count,
            "confidence": self.confidence,
            "notes": self.notes,
        }


def extract_quoted_title(question: str) -> str | None:
    for pat in (
        r"[«\"]([^»\"]{2,120})[»\"]",
        r"рассказа\s+[«\"]?([А-ЯЁA-Z][^»\"\n.]{2,80})",
        r"повести\s+[«\"]?([А-ЯЁA-Z][^»\"\n.]{2,80})",
        r"романа\s+[«\"]?([А-ЯЁA-Z][^»\"\n.]{2,80})",
        r"произведения\s+[«\"]?([А-ЯЁA-Z][^»\"\n.]{2,80})",
    ):
        m = re.search(pat, question or "", flags=re.I)
        if m:
            t = m.group(1).strip(" «»\"'.")
            if len(t) >= 2:
                return t
    return None


def detect_author(question: str) -> tuple[str | None, str | None]:
    qn = _norm(question)
    # Longer aliases first
    for ru, key in sorted(AUTHOR_ALIASES.items(), key=lambda x: -len(x[0])):
        if re.search(rf"(?<![а-яa-z]){re.escape(ru)}(?![а-яa-z])", qn):
            return ru, key
    return None, None


def parse_title_from_filename(name: str) -> str:
    """classic_0004_Pushkin_Евгений_Онегин_hash.txt → Евгений Онегин"""
    base = (name or "").rsplit(".", 1)[0]
    # drop trailing hash _[0-9a-f]{8,}
    base = re.sub(r"_[0-9a-f]{8,}$", "", base, flags=re.I)
    # drop fragment suffixes
    base = re.sub(r"__фрагмент_\d+.*$", "", base, flags=re.I)
    parts = base.split("_")
    # classic, NNNN, Author, Title...
    if len(parts) >= 4 and parts[0].lower().startswith("classic"):
        title_parts = parts[3:]
        return " ".join(title_parts).replace("  ", " ").strip(" _")
    # prefix_Author_Title
    if len(parts) >= 3:
        return " ".join(parts[2:]).replace("  ", " ").strip(" _")
    return base.replace("_", " ")


def analyze_query(question: str, *, prompt_mode: str = "general") -> QueryIntent:
    q = (question or "").strip()
    if "_____" in q or "___" in q or re.search(r"заполни\s+пропуск", q, re.I):
        return QueryIntent(intent="cloze", confidence=0.95, notes=["blank"])

    if prompt_mode == "cloze":
        # Still allow natural intents to override when clear.
        pass

    author_ru, author_key = detect_author(q)
    title = extract_quoted_title(q)
    qn = _norm(q)

    want_titles = bool(re.search(r"(назван|список|перечисл|вывед|какие)", qn))
    want_count = bool(re.search(r"(сколько|число|количество|кол-во)", qn))

    if author_key and (_CATALOG_RE.search(q) or want_count or want_titles) and not title:
        return QueryIntent(
            intent="catalog",
            author_ru=author_ru,
            author_key=author_key,
            want_titles=want_titles or True,
            want_count=want_count or True,
            confidence=0.9,
            notes=["catalog_by_author"],
        )

    if title and (_SUMMARY_RE.search(q) or re.search(r"(изложен|перескаж|о\s+ч[её]м)", qn)):
        return QueryIntent(
            intent="summarize",
            author_ru=author_ru,
            author_key=author_key,
            title=title,
            confidence=0.88,
            notes=["summary_of_title"],
        )

    if author_key and _OVERVIEW_RE.search(q) and not title:
        return QueryIntent(
            intent="author_overview",
            author_ru=author_ru,
            author_key=author_key,
            confidence=0.85,
            notes=["author_overview"],
        )

    if title and not _CATALOG_RE.search(q):
        # Question about a named work → prefer grounded answer / light summary
        if _SUMMARY_RE.search(q) or re.search(r"(изложен|содержан|сюжет)", qn):
            return QueryIntent(
                intent="summarize",
                author_ru=author_ru,
                author_key=author_key,
                title=title,
                confidence=0.8,
                notes=["title_focus"],
            )
        return QueryIntent(
            intent="extractive" if prompt_mode == "extractive" else "general",
            author_ru=author_ru,
            author_key=author_key,
            title=title,
            confidence=0.7,
            notes=["title_mentioned"],
        )

    if prompt_mode == "extractive":
        return QueryIntent(
            intent="extractive",
            author_ru=author_ru,
            author_key=author_key,
            title=title,
            confidence=0.55,
        )
    return QueryIntent(
        intent="general",
        author_ru=author_ru,
        author_key=author_key,
        title=title,
        confidence=0.5,
    )


def prompt_for_intent(intent: QueryIntent, question: str, numbered: list[str]) -> str:
    sources = "\n\n".join(numbered)
    if intent.intent == "author_overview":
        who = intent.author_ru or intent.author_key or "автора"
        return (
            f"Ты локальный литературоведческий ассистент. Вопрос о творчестве: {who}.\n"
            "Ответь по-русски, опираясь ТОЛЬКО на приведённые фрагменты из корпуса.\n"
            "Кратко (6–12 предложений): темы, стиль, характерные мотивы, опираясь на цитаты.\n"
            "Не выдумывай биографию и факты вне источников. Цитируй [n].\n"
            "Если фрагментов мало — скажи, что известно только по имеющимся текстам.\n"
            "think=false.\n\n"
            f"Вопрос: {question}\n\nИсточники:\n{sources}"
        )
    if intent.intent == "summarize":
        title = intent.title or "произведение"
        return (
            f"Составь краткое изложение произведения «{title}» по-русски.\n"
            "Используй ТОЛЬКО источники ниже (фрагменты корпуса). 5–10 предложений.\n"
            "Не добавляй сцены, которых нет в тексте. Цитируй [n] где уместно.\n"
            "Если текста недостаточно для полного изложения — так и напиши и изложи доступное.\n"
            "think=false.\n\n"
            f"Вопрос: {question}\n\nИсточники:\n{sources}"
        )
    if intent.intent == "extractive":
        return (
            "Ответь КОРОТКОЙ цитатой или точным фактом из источников.\n"
            "Без выдумок. Если нет в источниках — «не найдено в документах». [n]. think=false.\n\n"
            f"Вопрос: {question}\n\nИсточники:\n{sources}"
        )
    return (
        "Ты локальный RAG-ассистент. Отвечай по-русски только по источникам.\n"
        "Учитывай тип вопроса (факт / обзор / пересказ). Цитируй [n]. think=false.\n"
        "Если ответа нет — «не найдено в документах».\n\n"
        f"Вопрос: {question}\n\nИсточники:\n{sources}"
    )
