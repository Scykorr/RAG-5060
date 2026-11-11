"""Clean chunk text for the connectivity graph index (slice 1c).

Corpus files ship with an ingest-style banner that is *not* produced by
``apps.api.services.ingestion`` (chunk_text there only splits/sizes). Observed
formats in live Chunk.text:

1) Classic folder pack::

       Произведение русской классики
       Автор/папка: Bryusov
       Файл: Бемоль.txt

2) Classic literature pack::

       Произведение русской классической литературы
       Автор: Dostoevsky
       Название/часть: Братья Карамазовы__фрагмент_15

3) ``scripts/fetch-ru-corpus.sh`` emit::

       Произведение: …
       Автор: …
       Источник: …
       Фрагмент: i/n

All graph layers must run on ``clean_chunk_for_graph`` output only.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from apps.api.services.text_entities import norm_ru

# Labels that start a header field line (exact corpus markers).
_HEADER_LABELS = (
    "Автор/папка",
    "Автор",
    "Файл",
    "Папка",
    "Название/часть",
    "Название",
    "Источник",
    "Фрагмент",
    "Произведение",
)

_HEADER_LINE_RE = re.compile(
    r"^(?:"
    + "|".join(re.escape(x) for x in _HEADER_LABELS)
    + r")\s*:\s*(.*)$",
    flags=re.IGNORECASE | re.UNICODE,
)

_BANNER_RE = re.compile(
    r"^произведение\s+русской\s+класси",
    flags=re.IGNORECASE | re.UNICODE,
)

_EDITION_MARKERS = (
    "собрание сочинений",
    "полное собрание",
    "издательство",
    "художественная литература",
    "гихл",
    "наука,",
    "м.:",
    "л.:",
    "спб.:",
    "ocr",
    "cdrom",
    "cd-rom",
    "текст с ",
    "http://",
    "https://",
    "www.",
    "оглавление",
    "содержание",
    "действующие лица",
)

_URL_RE = re.compile(r"https?://|www\.", flags=re.I)
_BOM_RE = re.compile(r"^\ufeff+")
# Play stage directions: (целует её) inside dialogue; NAME IN CAPS. direction.
_PAREN_STAGE_RE = re.compile(r"\([^)]{2,80}\)")
_CAPS_SPEAKER_RE = re.compile(
    r"(?m)^[\t ]*([A-ZА-ЯЁ][A-ZА-ЯЁ\-]{1,30}(?:\s+[A-ZА-ЯЁ][A-ZА-ЯЁ\-]{1,30}){0,2})\.\s+([^\n]{0,120})",
)


def strip_stage_directions(text: str) -> str:
    """Remove structural play remakes from lex/entity text (not word lists).

    1) Parenthetical asides inside replicas: ``(целует её)``, ``(уходит)``.
    2) Lines ``ИМЯ ПЕРСОНАЖА. действие…`` — drop the direction after the name.
    """
    if not text:
        return ""
    out = _PAREN_STAGE_RE.sub(" ", text)

    def _caps_repl(m: re.Match[str]) -> str:
        name, rest = m.group(1), m.group(2)
        # Keep spoken line if it looks like dialogue (lowercase / quotes), drop pure stage
        rest_s = (rest or "").strip()
        if not rest_s:
            return name + "."
        # Stage-like: starts with verb infinitive/3p or short action phrase without quotes
        if rest_s[0].islower() and "«" not in rest_s and '"' not in rest_s and len(rest_s.split()) <= 12:
            return name + "."
        return m.group(0)

    out = _CAPS_SPEAKER_RE.sub(_caps_repl, out)
    return re.sub(r"[ \t]{2,}", " ", out)


def _as_meta(doc_meta: Mapping[str, Any] | None) -> dict[str, Any]:
    if not doc_meta:
        return {}
    if hasattr(doc_meta, "__dict__") and not isinstance(doc_meta, Mapping):
        # DocMeta dataclass
        d = getattr(doc_meta, "__dict__", {})
        return dict(d)
    return dict(doc_meta)


def _parse_header_block(lines: list[str]) -> tuple[dict[str, str], int]:
    """Return header_info and index of first non-header line."""
    info: dict[str, str] = {}
    i = 0
    n = len(lines)
    # Skip leading blanks / BOM
    while i < n and not lines[i].strip():
        i += 1
    if i >= n:
        return info, i

    first = lines[i].strip()
    if _BANNER_RE.match(norm_ru(first)) or norm_ru(first).startswith("произведение:"):
        # Banner without colon, or "Произведение: title" with colon handled below
        m = _HEADER_LINE_RE.match(first)
        if m and first.lower().startswith("произведение"):
            info["title"] = m.group(1).strip()
        i += 1
    elif _HEADER_LINE_RE.match(first):
        pass  # fall through to label loop
    else:
        # No known banner — maybe still has label lines later; only strip if
        # the very first non-empty line is a header label.
        return info, 0

    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s:
            # Blank line ends the header block once we already saw labels.
            if info:
                i += 1
                break
            i += 1
            continue
        m = _HEADER_LINE_RE.match(s)
        if not m:
            break
        label = s.split(":", 1)[0].strip().lower().replace("ё", "е")
        value = m.group(1).strip()
        if label in ("автор/папка", "автор"):
            info["author"] = value
        elif label in ("название/часть", "название", "произведение"):
            info["title"] = value
        elif label == "файл":
            info["file"] = value
            # Filename often holds the work title.
            if "title" not in info and value:
                stem = re.sub(r"\.(txt|md|pdf|docx?|fb2|epub)$", "", value, flags=re.I)
                info.setdefault("title_from_file", stem)
        elif label == "папка":
            info.setdefault("author", value)
        elif label == "источник":
            info["source"] = value
        elif label == "фрагмент":
            info["fragment"] = value
        i += 1

    # Consume one more blank after header if present
    while i < n and not lines[i].strip():
        i += 1
    return info, i


def _is_edition_line(line: str) -> bool:
    n = norm_ru(line)
    if not n.strip():
        return False
    if _URL_RE.search(line):
        return True
    return any(m in n for m in _EDITION_MARKERS)


def _author_needles(author: str | None) -> list[str]:
    """Latin keys and RU alias surfaces for matching title-page lines."""
    if not author:
        return []
    out: list[str] = []
    a = norm_ru(author)
    if a:
        out.append(a)
    try:
        from apps.api.services import query_intent as qi

        for surface, key in qi.AUTHOR_ALIASES.items():
            if norm_ru(key) == a or norm_ru(surface) == a:
                out.append(norm_ru(surface))
                out.append(norm_ru(key))
    except Exception:
        pass
    return sorted({x for x in out if len(x) >= 3}, key=len, reverse=True)


def _looks_like_author_title_line(line: str, author: str | None, title: str | None) -> bool:
    """True if line is mostly FIO + book title matching doc meta."""
    s = line.strip().strip("«»\"'")
    if not s or len(s) > 180:
        return False
    n = norm_ru(s)
    # "Александр Пушкин ЕГИПЕТСКИЕ НОЧИ ГЛАВА I" / "Валерий Брюсов. Бемоль"
    title_n = norm_ru(title or "")
    title_n = re.sub(r"__фрагмент_\d+.*$", "", title_n).strip()
    title_n = re.sub(r"\.(txt|md)$", "", title_n).strip()

    hits = 0
    needles = _author_needles(author)
    if needles and any(t in n for t in needles):
        hits += 1
    if title_n:
        title_core = re.sub(r"[^\wа-яё]+", " ", title_n, flags=re.U).strip()
        title_tokens = [t for t in title_core.split() if len(t) >= 3]
        if title_tokens and all(t in n for t in title_tokens[:3]):
            hits += 1
        elif title_core and title_core in n:
            hits += 1
    if hits >= 2:
        return True
    if hits == 1 and ("." in s or s.isupper() or len(s.split()) <= 8):
        if title_n and (title_n in n or any(t in n for t in title_n.split() if len(t) >= 4)):
            return True
        if needles and any(t in n for t in needles):
            return True
    letters = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", s)
    if title_n and letters and letters == letters.upper() and len(letters) >= 4:
        tletters = re.sub(r"[^a-zа-яё]", "", title_n)
        if tletters and tletters in norm_ru(letters):
            return True
        if tletters and norm_ru(letters) in tletters:
            return True
    return False


def _strip_front_matter(
    lines: list[str],
    *,
    author: str | None,
    title: str | None,
    max_lines: int = 12,
) -> tuple[list[str], int]:
    """Drop book title / edition / TOC lines at the start of early chunks."""
    removed = 0
    i = 0
    n = len(lines)
    while i < n and i < max_lines:
        s = lines[i].strip()
        if not s:
            i += 1
            removed += 1
            continue
        # Roman-numeral-only chapter markers right after title
        if re.fullmatch(r"[IVXLCDM]+", s):
            i += 1
            removed += 1
            continue
        if _is_edition_line(s) or _looks_like_author_title_line(s, author, title):
            i += 1
            removed += 1
            continue
        # Standalone short ALL-CAPS title
        letters = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", s)
        if letters and letters == letters.upper() and 3 <= len(letters) <= 60:
            title_n = norm_ru(title or "")
            if title_n and norm_ru(letters) in re.sub(r"[^a-zа-яё]", "", title_n):
                i += 1
                removed += 1
                continue
            if title_n and re.sub(r"[^a-zа-яё]", "", title_n) in norm_ru(letters):
                i += 1
                removed += 1
                continue
        break
    # Drop trailing blanks left at the new start
    while i < n and not lines[i].strip():
        i += 1
        removed += 1
    return lines[i:], removed


def clean_chunk_for_graph(
    text: str,
    chunk_ordinal: int = 0,
    doc_meta: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Strip ingest banners and early book front-matter for graph analysis.

    Returns ``(clean_text, header_info)`` where ``header_info`` may contain
    ``author``, ``title``, ``file``, ``source``, ``fragment``,
    ``chars_removed``, ``header_stripped``, ``front_matter_stripped``.
    """
    raw = text or ""
    meta = _as_meta(doc_meta)
    header_info: dict[str, Any] = {
        "chars_removed": 0,
        "header_stripped": False,
        "front_matter_stripped": False,
    }
    if not raw.strip():
        return "", header_info

    # Normalize newlines; keep content otherwise intact.
    body = raw.replace("\r\n", "\n").replace("\r", "\n")
    body = _BOM_RE.sub("", body)
    lines = body.split("\n")

    info, hdr_end = _parse_header_block(lines)
    if info or hdr_end > 0:
        header_info["header_stripped"] = True
        for k, v in info.items():
            header_info[k] = v
        lines = lines[hdr_end:]

    author = (
        header_info.get("author")
        or meta.get("author_key")
        or meta.get("author_ru")
        or meta.get("author")
    )
    title = (
        header_info.get("title")
        or header_info.get("title_from_file")
        or meta.get("title")
    )
    # Prefer human title over fragment-suffixed name
    if isinstance(title, str):
        title = re.sub(r"__фрагмент_\d+.*$", "", title, flags=re.I).strip()

    if int(chunk_ordinal or 0) <= 1:
        before = len(lines)
        lines, _rm = _strip_front_matter(lines, author=str(author) if author else None, title=str(title) if title else None)
        if len(lines) < before:
            header_info["front_matter_stripped"] = True

    clean = "\n".join(lines).strip()
    clean = strip_stage_directions(clean)
    # Collapse runs of blank lines left by stripping
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    header_info["chars_removed"] = max(0, len(raw) - len(clean))
    if "author" in header_info and isinstance(header_info["author"], str):
        # Drop placeholder folder names that are not real authors
        if norm_ru(header_info["author"]) in {"russian", "books", "english", "classic"}:
            header_info.pop("author", None)
    return clean, header_info
