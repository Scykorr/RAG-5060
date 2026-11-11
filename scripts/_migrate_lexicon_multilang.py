#!/usr/bin/env python3
"""One-shot: wrap flat graph_lexicon.json into {ru, en, shared}."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "configs" / "graph_lexicon.json"

EN = {
    "genre_markers": [
        "novel", "novels", "story", "stories", "poem", "poems", "play", "plays",
        "chapter", "chapters", "essay", "essays", "book", "books",
    ],
    "genre_title_blocklist": ["stories", "poems", "novels", "plays", "essays", "books"],
    "play_stage_words": [
        "act", "scene", "exit", "enter", "curtain", "pause", "stage", "voice", "voices", "silence"
    ],
    "edition_markers": [
        "table of contents", "copyright", "all rights reserved", "published by",
        "http://", "https://", "www.", "ocr",
    ],
    "commentary_markers": [
        "notes", "commentary", "editor's note", "footnote", "endnotes", "see also",
    ],
    "speech_verbs": [
        "said", "says", "asked", "replied", "answered", "whispered", "shouted",
        "exclaimed", "cried", "muttered", "added", "continued",
    ],
    "style_burst_hints": [
        "suddenly", "slightly", "quietly", "slowly", "quickly", "perhaps", "indeed",
        "enter", "exit", "pause",
    ],
    "list_query_markers": [
        "list", "enumerate", "which works", "what are the", "name all", "all works",
    ],
    "question_title_starters": [
        "who", "what", "where", "when", "why", "how", "which", "whose",
    ],
    "editorial_stop_terms": [
        "footnote", "note", "notes", "ed", "vol", "chapter", "page", "pp",
    ],
    "lex_stopwords": [
        "the", "and", "for", "that", "with", "this", "from", "have", "been",
        "were", "are", "was", "will", "would", "could", "should", "into", "than",
        "then", "them", "they", "their", "there", "these", "those", "what", "when",
        "where", "which", "while", "about", "after", "before", "under", "over",
        "such", "only", "other", "some", "more", "most", "also", "just", "like",
        "very", "each", "many", "much", "shall", "must", "may", "can", "not",
    ],
    "function_words": [
        "the", "and", "of", "to", "in", "a", "is", "that", "for", "on", "with",
        "as", "by", "at", "from", "or", "an", "be", "this", "which", "are",
    ],
    "tech_ref_markers": [
        "see", "see also", "refer to", "according to", "per", "as defined in",
        "specified in", "described in", "section", "clause", "figure", "table",
        "appendix", "rfc", "iso", "ieee",
    ],
    "place_lemmas": [],
    "god_forms": [],
    "author_first_names": [],
    "author_name_table": {},
    "diminutive_suffixes": [],
    "diminutive_names": [],
    "entity_interjections": ["please", "sorry", "excuse"],
    "lex_cross_theme_stems": [
        "encrypt", "cipher", "handshake", "certificate", "protocol", "payload",
        "endpoint", "authent", "authoriz", "session", "traffic",
    ],
    "ambiguous_author_surnames": [],
}

SHARED = {
    "ru_function_words": [
        "и", "в", "во", "на", "с", "со", "к", "ко", "у", "о", "об", "от", "до",
        "по", "из", "за", "для", "при", "не", "но", "а", "как", "что", "это",
        "он", "она", "они", "мы", "вы", "его", "её", "их", "был", "была", "были",
    ],
    "en_function_words": [
        "the", "and", "of", "to", "in", "a", "is", "that", "for", "on", "with",
        "as", "by", "at", "from", "or", "an", "be", "this", "which", "are", "was",
    ],
}


def main() -> None:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    if isinstance(data.get("ru"), dict) and isinstance(data.get("en"), dict):
        print("already nested")
        return
    skip = {"comment"}
    ru = {k: v for k, v in data.items() if k not in skip}
    # ensure ru lex_stopwords / function_words
    ru.setdefault(
        "lex_stopwords",
        [
            "это", "как", "так", "для", "или", "что", "чтобы", "когда", "если",
            "только", "уже", "еще", "ещё", "очень", "было", "были", "была",
        ],
    )
    ru.setdefault("function_words", list(SHARED["ru_function_words"]))
    ru.setdefault(
        "tech_ref_markers",
        ["см", "см.", "смотри", "см также", "в соответствии с", "согласно", "раздел", "глава", "гост", "rfc"],
    )
    out = {
        "comment": "Language-scoped rules for connectivity graph. Offline — no downloads. Nested: ru / en / shared.",
        "ru": ru,
        "en": EN,
        "shared": SHARED,
    }
    PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", PATH, "ru_keys", len(ru), "en_keys", len(EN))


if __name__ == "__main__":
    main()
