"""Multilingual helpers for connectivity index (slice 1e-M).

Offline, stdlib-only: language detect, EN suffix stemmer, doc_kind heuristics,
language-agnostic technical identifiers (RFC/ISO/GOST/versions/code ids).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Literal

from apps.api.services import graph_lexicon as glex

Lang = Literal["ru", "en", "mixed", "other", "unknown"]
DocKind = Literal["fiction", "nonfiction", "technical", "unknown"]

_CYR = re.compile(r"[А-Яа-яЁё]")
_LAT = re.compile(r"[A-Za-z]")
_LETTER = re.compile(r"[A-Za-zА-Яа-яЁё]")
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё']+")
_SECTION_NUM = re.compile(r"\b\d+(?:\.\d+){1,4}\b")
_CODEISH = re.compile(
    r"(```|def\s+\w+\s*\(|function\s+\w+|#!/|console\.|kubectl\s|curl\s|"
    r"include\s*<|SELECT\s+.+\s+FROM|npm\s+install|pip\s+install)",
    re.I,
)

# Standards / versions / code identifiers (language-agnostic)
_RFC = re.compile(r"\bRFC[\s-]?\d{3,5}\b", re.I)
_ISO = re.compile(r"\bISO(?:/[A-Z]+)?[\s-]?\d+(?:[\s:-]?\d+)*(?:[\s:-]?[A-Z0-9]+)?\b", re.I)
_IEEE = re.compile(r"\bIEEE[\s-]?\d+(?:\.\d+)*\b", re.I)
_GOST = re.compile(r"\bГОСТ(?:\s*Р)?[\s-]?\d+(?:\.\d+)*(?:[\s-]?\d+)?\b", re.I)
_TLS_SSL = re.compile(r"\b(?:TLS|SSL)[\s-]?\d+(?:\.\d+)+\b", re.I)
_VERSION = re.compile(r"\bv?\d+\.\d+(?:\.\d+){0,3}\b", re.I)
_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")
_CAMEL = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-zA-Z0-9]+)+\b")
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_FUNC = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(")
_PATH = re.compile(
    r"(?:/(?:usr|var|etc|opt|home|tmp|app|src|lib|bin|sbin|mnt|data)(?:/[A-Za-z0-9._-]+)+)"
    r"|[A-Za-z]:\\[^\s]+"
)
_CLI = re.compile(r"\b(?:kubectl|openssl|curl|docker|git|npm|pip|systemctl)\b", re.I)
_ROMAN = re.compile(r"^[IVXLCDM]{2,12}$")
_ACRONYM_STOP = frozenset(
    {
        "THE",
        "AND",
        "FOR",
        "WITH",
        "FROM",
        "THIS",
        "THAT",
        "HTML",
        "MUST",
        "SHALL",
        "MAY",
        "NOT",
        "ALL",
        "ANY",
        "BUT",
        "WHO",
        "HOW",
        "WHY",
        "YES",
        "NO",
        "OK",
        "PDF",
        "TXT",
        "URL",
        "WWW",
        "HTTP",
        "HTTPS",
        "OCR",
        "NN",
        "SS",
        "MM",
        "QQ",
        "XX",
        "YY",
        "ZZ",
        "CDROM",
        "ISBN",
        "ISSN",
    }
)
_TECH_ACRONYM_KEEP = frozenset(
    {
        "API",
        "TLS",
        "SSL",
        "RFC",
        "ISO",
        "IEEE",
        "TCP",
        "UDP",
        "IP",
        "DNS",
        "HTTP",
        "HTTPS",
        "JSON",
        "XML",
        "YAML",
        "SQL",
        "CLI",
        "SDK",
        "GPU",
        "CPU",
        "RAM",
        "AES",
        "GCM",
        "PKI",
        "JWT",
        "OAUTH",
        "LDAP",
        "SSH",
        "VPN",
        "URL",
        "URI",
        "UUID",
        "GUID",
        "HTML",
        "CSS",
        "WASM",
    }
)

_EN_SUFFIXES = (
    "ational",
    "tional",
    "enci",
    "anci",
    "izer",
    "alli",
    "entli",
    "eli",
    "ousli",
    "ization",
    "ation",
    "ator",
    "alism",
    "iveness",
    "fulness",
    "ousness",
    "aliti",
    "iviti",
    "biliti",
    "ement",
    "ance",
    "ence",
    "able",
    "ible",
    "ment",
    "ness",
    "tion",
    "sion",
    "ies",
    "ied",
    "ing",
    "ers",
    "ied",
    "ly",
    "ed",
    "es",
    "s",
)

_TECH_MARKERS_EN = frozenset(
    {
        "rfc",
        "iso",
        "ieee",
        "api",
        "tls",
        "ssl",
        "http",
        "https",
        "specification",
        "spec",
        "protocol",
        "configuration",
        "guide",
        "manual",
        "reference",
        "implementation",
        "endpoint",
        "payload",
        "schema",
        "version",
        "chapter",
        "section",
        "figure",
        "table",
        "appendix",
        "clause",
    }
)
_TECH_MARKERS_RU = frozenset(
    {
        "гост",
        "спецификац",
        "руководств",
        "протокол",
        "конфигурац",
        "раздел",
        "приложение",
        "стандарт",
        "техническ",
        "api",
        "tls",
        "rfc",
        "версия",
    }
)
_FICTION_MARKERS_RU = frozenset(
    {
        "рассказ",
        "повесть",
        "роман",
        "глава",
        "герой",
        "сказала",
        "сказал",
        "вскричал",
        "действие",
        "явление",
    }
)
_FICTION_MARKERS_EN = frozenset(
    {
        "chapter",
        "said",
        "novel",
        "story",
        "character",
        "scene",
        "act",
    }
)


def _detect_words(lang: str) -> frozenset[str]:
    block = glex.lang_block("shared")
    key = f"{lang}_function_words"
    raw = block.get(key) or glex.lang_block(lang).get("function_words") or []
    return frozenset(str(x).lower() for x in raw)


def letter_script_ratio(text: str) -> tuple[float, float, int]:
    """Return (cyr_share, lat_share, letter_count)."""
    letters = _LETTER.findall(text or "")
    if not letters:
        return 0.0, 0.0, 0
    cyr = sum(1 for ch in letters if _CYR.match(ch))
    lat = sum(1 for ch in letters if _LAT.match(ch))
    n = len(letters)
    return cyr / n, lat / n, n


def detect_language(text: str) -> Lang:
    """Heuristic language: ru / en / mixed / other / unknown.

    Latin script alone is not English — fr/de (and other Latin) → ``other``.
    """
    text = text or ""
    cyr, lat, n = letter_script_ratio(text)
    if n < 12:
        return "unknown"
    words = [w.lower() for w in _WORD.findall(text)]
    ru_fw = _detect_words("ru")
    en_fw = _detect_words("en")
    fr_fw = _detect_words("fr")
    de_fw = _detect_words("de")
    ru_hits = sum(1 for w in words if w in ru_fw)
    en_hits = sum(1 for w in words if w in en_fw)
    fr_hits = sum(1 for w in words if w in fr_fw)
    de_hits = sum(1 for w in words if w in de_fw)
    id_n = len(extract_identifiers(text))
    codeish = bool(_CODEISH.search(text))
    # Mixed: both scripts present with meaningful mass, or RU prose + code/ids
    if cyr >= 0.20 and lat >= 0.20:
        # French/German dialogue islands inside Russian → still mixed at doc level;
        # chunk-level callers see per-chunk lang.
        return "mixed"
    if cyr >= 0.40 and lat >= 0.08 and (codeish or id_n >= 2 or en_hits >= 2):
        return "mixed"
    if cyr >= 0.55 or (cyr >= 0.35 and ru_hits >= en_hits):
        return "ru"
    if lat >= 0.35:
        # Distinctive markers beat raw function-word overlap (de/la/un collide).
        fr_mark = sum(
            1
            for w in words
            if w in {"dans", "pour", "avec", "cette", "comme", "mais", "nous", "vous", "elle", "est", "que", "qui", "pas", "aux", "une", "les", "des", "mon", "son", "lui", "tout", "guerre", "homme", "femme"}
        )
        de_mark = sum(
            1
            for w in words
            if w in {"nicht", "auch", "oder", "aber", "wenn", "noch", "schon", "meine", "dein", "und", "ich", "sie", "der", "die", "das", "ein", "eine", "mit", "auf"}
        )
        en_mark = sum(
            1
            for w in words
            if w in {"the", "and", "with", "that", "this", "from", "have", "was", "are", "which", "for", "not", "you", "they"}
        )
        if fr_mark >= 2 and fr_mark >= en_mark:
            return "other"
        if de_mark >= 2 and de_mark >= en_mark:
            return "other"
        # Prefer fr/de over en when their function words dominate
        if fr_hits >= 2 and fr_hits >= en_hits and fr_hits >= de_hits:
            return "other"
        if de_hits >= 2 and de_hits >= en_hits:
            return "other"
        if en_mark >= 2 and en_mark > fr_mark and en_mark > de_mark:
            return "en"
        if en_hits > fr_hits and en_hits > de_hits and (en_hits >= 2 or lat >= 0.55):
            return "en"
        if fr_hits + de_hits > en_hits:
            return "other"
        if en_hits >= 1 and lat >= 0.55 and fr_mark == 0 and de_mark == 0:
            return "en"
        if lat >= 0.55:
            return "other"
    if cyr > lat and cyr >= 0.25:
        return "ru"
    if lat > cyr and lat >= 0.25:
        return "other" if en_hits == 0 else "en"
    return "unknown"


def stem_en(token: str) -> str:
    """Simple English suffix stemmer (Porter-ish lite, stdlib only)."""
    n = (token or "").lower()
    if len(n) < 5:
        return n
    for end in _EN_SUFFIXES:
        if n.endswith(end) and len(n) - len(end) >= 3:
            return n[: -len(end)]
    return n


def stem_token(token: str, lang: str) -> str:
    from apps.api.services.text_entities import stem_ru

    if lang == "en":
        return stem_en(token)
    return stem_ru(token)


def tokenize_lex_lang(text: str, lang: str) -> list[str]:
    """Lex tokens for a language: letters ≥4, stemmed, stop/style filtered."""
    from apps.api.services.graph_text_clean import normalize_homoglyphs
    from apps.api.services.text_entities import norm_ru

    if lang == "en":
        # Pure English path: do not rewrite Latin lookalikes.
        raw = re.findall(r"[A-Za-z]{4,}", text or "")
        stops = glex.lang_frozenset("en", "lex_stopwords") | glex.lang_frozenset(
            "en", "style_burst_hints"
        ) | glex.lang_frozenset("en", "speech_verbs") | glex.lang_frozenset(
            "en", "play_stage_words"
        )
        out: list[str] = []
        for t in raw:
            key = t.lower()
            if key in stops:
                continue
            out.append(stem_en(key))
        return out
    # ru / mixed / unknown → Russian pipeline (mixed uses caller's chunk lang)
    cleaned, _ = normalize_homoglyphs(text or "")
    raw = re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", cleaned, flags=re.UNICODE)
    stops = glex.lang_frozenset("ru", "lex_stopwords") | glex.lang_frozenset(
        "ru", "style_burst_hints"
    ) | glex.lang_frozenset("ru", "speech_verbs") | glex.lang_frozenset(
        "ru", "play_stage_words"
    ) | glex.lang_frozenset("ru", "editorial_stop_terms")
    from apps.api.services.text_entities import stem_ru

    out = []
    for t in raw:
        key = norm_ru(t)
        if any(ch.isdigit() for ch in key):
            continue
        if key in stops:
            continue
        # Skip pure Latin in ru tokenization (belongs to en / identifiers)
        if re.fullmatch(r"[a-z]+", key) and lang == "ru":
            continue
        out.append(stem_ru(key))
    return out


def extract_identifiers(text: str) -> list[tuple[str, str]]:
    """Return list of (surface, kind) for language-agnostic technical entities."""
    text = text or ""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(surface: str, kind: str) -> None:
        s = re.sub(r"\s+", " ", surface).strip()
        if len(s) < 2:
            return
        compact = re.sub(r"[\s_-]+", "", s).upper()
        if _ROMAN.fullmatch(compact):
            return
        key = s.upper() if kind in {"acronym", "standard"} else s
        if key in seen:
            return
        seen.add(key)
        found.append((s, kind))

    for rx, kind in (
        (_RFC, "standard"),
        (_ISO, "standard"),
        (_IEEE, "standard"),
        (_GOST, "standard"),
        (_TLS_SSL, "standard"),
    ):
        for m in rx.finditer(text):
            add(m.group(0), kind)
    for m in _VERSION.finditer(text):
        # skip bare section numbers already counted; keep product-like versions
        g = m.group(0)
        if _SECTION_NUM.fullmatch(g) and g.count(".") >= 2 and not g.lower().startswith("v"):
            continue
        add(g, "version")
    for m in _CAMEL.finditer(text):
        add(m.group(0), "identifier")
    for m in _SNAKE.finditer(text):
        if len(m.group(0)) >= 6:
            add(m.group(0), "identifier")
    for m in _FUNC.finditer(text):
        name = m.group(0).rstrip("(").strip()
        if len(name) >= 3 and name.lower() not in {"if", "for", "while", "return"}:
            add(name, "identifier")
    for m in _PATH.finditer(text):
        add(m.group(0)[:80], "identifier")
    for m in _CLI.finditer(text):
        add(m.group(0), "identifier")
    # Acronyms: allowlist of tech tokens (fiction ALLCAPS/OCR otherwise floods edges)
    for m in _ACRONYM.finditer(text):
        g = m.group(0)
        if g in _ACRONYM_STOP or _ROMAN.fullmatch(g):
            continue
        if g not in _TECH_ACRONYM_KEEP:
            continue
        add(g, "acronym")
    return found


def detect_doc_kind(text: str, *, language: str, filename: str = "") -> DocKind:
    """Heuristic document kind."""
    text = text or ""
    low = text.lower()
    fn = (filename or "").lower()
    ids = extract_identifiers(text)
    id_n = len(ids)
    section_n = len(_SECTION_NUM.findall(text))
    code = bool(_CODEISH.search(text))
    tech_hits = 0
    if language in {"en", "mixed", "unknown"}:
        tech_hits += sum(1 for w in _WORD.findall(low) if w in _TECH_MARKERS_EN)
    if language in {"ru", "mixed", "unknown"}:
        tech_hits += sum(1 for w in _WORD.findall(low) if any(w.startswith(t) for t in _TECH_MARKERS_RU))
    fiction_hits = 0
    if language == "ru":
        fiction_hits = sum(1 for w in _WORD.findall(low) if any(w.startswith(t) for t in _FICTION_MARKERS_RU))
    elif language == "en":
        fiction_hits = sum(1 for w in _WORD.findall(low) if w in _FICTION_MARKERS_EN)

    score_tech = id_n * 2 + section_n + (4 if code else 0) + tech_hits + (
        3 if any(x in fn for x in ("spec", "rfc", "guide", "manual", "api", "tls")) else 0
    )
    score_fiction = fiction_hits + (
        3 if any(x in fn for x in ("classic_", "novel", "story", "рассказ", "повесть")) else 0
    )
    if score_tech >= 8 and score_tech > score_fiction + 2:
        return "technical"
    if score_fiction >= 4 and score_fiction >= score_tech:
        return "fiction"
    if tech_hits >= 3 or section_n >= 5:
        return "nonfiction"
    if language == "en" and score_tech >= 4:
        return "technical"
    return "unknown"


def strip_doc_version(title: str) -> tuple[str, str | None]:
    """Split 'Spec v1.2' → ('Spec', '1.2')."""
    t = (title or "").strip()
    m = re.search(r"(?i)(?:\s|_|-)v?(\d+\.\d+(?:\.\d+){0,2})\s*$", t)
    if not m:
        m = re.search(r"(?i)\bv(?:ersion)?[\s_-]?(\d+\.\d+(?:\.\d+){0,2})\b", t)
        if not m:
            return t, None
        ver = m.group(1)
        base = (t[: m.start()] + t[m.end() :]).strip(" -_")
        return base or t, ver
    ver = m.group(1)
    base = t[: m.start()].strip(" -_")
    return base or t, ver


def identifier_key(surface: str) -> str:
    s = re.sub(r"\s+", " ", (surface or "").strip())
    return s.upper().replace(" ", "")


def aggregate_doc_language(chunk_langs: list[str]) -> Lang:
    if not chunk_langs:
        return "unknown"
    c = Counter(chunk_langs)
    # ignore unknown / other in majority for primary lang; other alone stays other
    useful = Counter({k: v for k, v in c.items() if k not in {"unknown"}})
    if not useful:
        return "unknown"
    content = Counter({k: v for k, v in useful.items() if k != "other"})
    if "ru" in content and "en" in content:
        return "mixed"
    if content:
        top = content.most_common(1)[0][0]
        # RU novel with French islands → still ru (or mixed if latin mass high)
        if top == "ru" and useful.get("other", 0) >= max(2, int(0.15 * sum(useful.values()))):
            return "mixed"
        return top  # type: ignore[return-value]
    return "other"


def literary_rules_enabled(doc_kind: str, language: str) -> bool:
    """Имя/Отчество and fiction genre markers only for fiction/ru unknown literary."""
    if language == "en":
        return False
    if doc_kind == "technical":
        return False
    if doc_kind in {"fiction", "unknown", "nonfiction"}:
        return language in {"ru", "mixed", "unknown"}
    return False
