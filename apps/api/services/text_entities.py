"""Shared RU text helpers for chat retrieval and graph connectivity index.

Moved from apps.api.routers.chat so graph_index can reuse the same stop-lists
and entity heuristics without changing chat behaviour.
"""

from __future__ import annotations

import re

ENTITY_DENY = {
    "документ",
    "документы",
    "вопрос",
    "вопросы",
    "пропуск",
    "источник",
    "источники",
    "автор",
    "фрагмент",
    "ответ",
    "текст",
    "новости",
    "highlight",
    "cloze",
    "id",
    "агентство",
    "сообщает",
    "потом",
    "она",
    "он",
    "они",
    "это",
    "также",
    "президент",
    "президента",
    "международной",
    "верховной",
    "рады",
    "однако",
    "профессор",
    "дискуссии",
    "взрыв",
    "географ",
    "кубок",
    "известия",
    "новое",
    "согласно",
    "после",
    "кроме",
    "между",
    "фрагменты",
    "зеленые",
    "фильм",
    "коми",
    "риа",
    "хдс",
    "тасс",
    "sputnik",
    "reuters",
    "afp",
    "dpa",
    "туризм",
    "партия",
    "партию",
    "кубка",
    "конфедераций",
    "москве",
    "берлине",
    "полузащитник",
    "переехавшего",
    "клуб",
    "судья",
    "эксперты",
    "банки",
    "рядом",
    "теперь",
    "адвокат",
    "дело",
    "российские",
    "клиентам",
    "ленты",
    "объявлен",
    "вторичное",
}

STOP = {
    "the",
    "and",
    "for",
    "with",
    "what",
    "when",
    "where",
    "which",
    "that",
    "this",
    "from",
    "have",
    "has",
    "are",
    "was",
    "were",
    "how",
    "why",
    "who",
    "into",
    "about",
    "your",
    "our",
    "их",
    "это",
    "как",
    "что",
    "или",
    "для",
    "ответь",
    "ответьте",
    "одним",
    "именем",
    "короткой",
    "фразой",
    "текста",
    "тексте",
    "автор",
    "автора",
    "подсказка",
    "слов",
    "рядом",
    "заполни",
    "пропуск",
    "пропуска",
    "русской",
    "русская",
    "классике",
    "классики",
    "классика",
    "документах",
    "источниках",
    "вопрос",
    "вопросы",
    "место",
    "неизвестен",
}

# Extra stop phrases for refers_to phrase matching (short / noisy).
REFERS_STOP_PHRASES = {
    "том",
    "том 1",
    "том 2",
    "часть",
    "глава",
    "рассказ",
    "повесть",
    "роман",
    "стихи",
    "сборник",
    "фрагмент",
    "sample",
    "sample a",
    "sample b",
    "txt",
    "pdf",
    "doc",
}


def norm_ru(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def tokenize(text: str) -> list[str]:
    """Simple lexical tokenizer: unicode letters/digits length ≥2, stop-filtered."""
    raw = re.findall(r"[0-9A-Za-zА-Яа-яЁё]{2,}", text or "", flags=re.UNICODE)
    out: list[str] = []
    for t in raw:
        key = norm_ru(t)
        if len(key) < 2:
            continue
        if key in STOP or key in ENTITY_DENY:
            continue
        out.append(key)
    return out


def split_passage_highlights(text: str) -> tuple[str, str]:
    """Split body vs @highlight lines; drop cloze meta tails."""
    t = text or ""
    for marker in (
        "\ncloze:",
        "\nвопросы с пропуском",
        "\nВопросы с пропуском",
        "\nсвязанный вопрос",
    ):
        idx = norm_ru(t).find(norm_ru(marker.strip()))
        if idx > 40:
            t = t[:idx]
            break
    highs = re.findall(r"(?mi)^@highlight\s*\n?(.*)$", t)
    body = re.sub(r"(?mi)^@highlight\s*\n?.*$", "", t)
    body = re.sub(r"@highlight\s*", " ", body)
    return body.strip(), "\n".join(h.strip() for h in highs if h.strip())


def passage_only(text: str) -> str:
    """Drop training meta; keep body + highlights for NER."""
    body, highs = split_passage_highlights(text)
    return body if not highs else f"{body}\n{highs}"


def extract_entity_candidates(texts: list[str], limit: int = 40) -> list[str]:
    """Surface forms likely to fill a RuCoS-style blank."""
    found: list[str] = []
    seen: set[str] = set()
    # Avoid \\b — it does not treat Cyrillic as word chars in Python re.
    pat = re.compile(
        r"(?<![0-9A-Za-zА-Яа-яЁё])(?:"
        r"[A-Z]{2,6}|"  # Latin acronyms USA-like
        r"[А-ЯЁ]{2,6}|"  # Cyrillic acronyms США/ФСБ
        r"[A-Z][A-Za-z0-9\-\.]{1,40}|"
        r"[А-ЯЁ][а-яёА-ЯЁ\-]{2,40}(?:\s+[А-ЯЁ][а-яёА-ЯЁ\-]{2,40}){0,1}"
        r")(?![0-9A-Za-zА-Яа-яЁё])"
    )
    for text in texts:
        body = passage_only(text)
        for m in pat.findall(body):
            s = m.strip(" .,:;\"'«»")
            key = norm_ru(s)
            if len(key) < 3 or key in seen:
                continue
            if key in STOP or key in ENTITY_DENY:
                continue
            if key.startswith("http"):
                continue
            seen.add(key)
            found.append(s)
            if len(found) >= limit:
                return found
    return found


# Back-compat aliases matching chat.py private names
_STOP = STOP
_ENTITY_DENY = ENTITY_DENY
_norm_ru = norm_ru
_extract_entity_candidates = extract_entity_candidates
_passage_only = passage_only
_split_passage_highlights = split_passage_highlights
