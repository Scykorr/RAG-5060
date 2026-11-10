"""Author mention + text-header detection (slice 1d A4/A5)."""

from __future__ import annotations

import re
from typing import Any

from apps.api.services import query_intent as qi
from apps.api.services.graph_lexicon import dict_field
from apps.api.services.text_entities import norm_ru, stem_ru

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+", re.UNICODE)
_INIT_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё])"
    r"((?:[A-ZА-ЯЁ]\.\s*){1,3})"
    r"([A-ZА-ЯЁ][a-zа-яёA-ZА-ЯЁ]{2,40})"
    r"(?![0-9A-Za-zА-Яа-яЁё\-])",
    re.UNICODE,
)


def author_name_table() -> dict[str, dict[str, Any]]:
    return dict_field("author_name_table")


def _surname_forms(key: str) -> set[str]:
    forms = {norm_ru(s) for s, k in qi.AUTHOR_ALIASES.items() if k == key}
    forms.add(norm_ru(key))
    table = author_name_table().get(key) or {}
    for s in table.get("surnames") or []:
        forms.add(norm_ru(s))
    return forms


def _match_surname(surface_n: str, key: str) -> bool:
    for s in _surname_forms(key):
        if surface_n == s:
            return True
        if len(s) >= 4 and surface_n.startswith(stem_ru(s)) and abs(len(surface_n) - len(s)) <= 3:
            return True
    return False


def _initials_ok(raw_initials: str, key: str) -> bool:
    table = author_name_table().get(key) or {}
    allowed_raw = [norm_ru(x).replace(" ", "") for x in (table.get("initials") or [])]
    if not allowed_raw:
        return False
    parts = re.findall(r"[a-zа-яё]", norm_ru(raw_initials))
    if not parts:
        return False
    got = ".".join(parts)
    got_set = {got, got + ".", "".join(parts), ".".join(parts) + "."}
    for a in allowed_raw:
        a_parts = re.findall(r"[a-zа-яё]", a)
        a_join = ".".join(a_parts)
        allowed_set = {a_join, a_join + ".", "".join(a_parts), a, a.rstrip(".")}
        # prefix match: «Л.» ok for author with initials «л» or «л.н»
        if got_set & allowed_set:
            return True
        if len(parts) == 1 and a_parts and parts[0] == a_parts[0]:
            return True
        if len(parts) >= 2 and a_parts[: len(parts)] == parts:
            return True
    return False


def _preceding_initials(raw: str, idx: int) -> str | None:
    """Return initials string immediately before idx, if any."""
    pre = raw[:idx].rstrip()
    m = re.search(r"((?:[A-ZА-ЯЁ]\.\s*){1,3})$", pre)
    return m.group(1) if m else None


def _first_name_ok(token_n: str, key: str) -> bool:
    table = author_name_table().get(key) or {}
    firsts = {norm_ru(x) for x in (table.get("first") or [])}
    pats = {norm_ru(x) for x in (table.get("patronymic") or [])}
    if token_n in firsts or token_n in pats:
        return True
    for f in firsts | pats:
        if len(f) >= 3 and token_n.startswith(stem_ru(f)) and abs(len(token_n) - len(f)) <= 3:
            return True
    return False


def _nearby_tokens(raw: str, start: int, end: int, radius: int = 3) -> list[str]:
    tokens = list(_TOKEN_RE.finditer(raw or ""))
    hit = [i for i, m in enumerate(tokens) if not (m.end() <= start or m.start() >= end)]
    if not hit:
        return []
    lo, hi = min(hit), max(hit)
    out = []
    for i, m in enumerate(tokens):
        if lo - radius <= i <= hi + radius and not (lo <= i <= hi):
            out.append(norm_ru(m.group(0)))
    return out


def find_author_mentions(raw: str) -> list[dict[str, Any]]:
    """Strict author mentions: capitalized only; wrong initials/names rejected."""
    if not raw:
        return []
    forms_by_key: dict[str, set[str]] = {}
    for surface, key in qi.AUTHOR_ALIASES.items():
        forms_by_key.setdefault(key, set()).add(norm_ru(surface))
    for key in author_name_table():
        forms_by_key.setdefault(key, set()).update(_surname_forms(key))

    hits: list[dict[str, Any]] = []

    for m in _INIT_RE.finditer(raw):
        initials, surname = m.group(1), m.group(2)
        after = raw[m.end() : m.end() + 1]
        before = raw[m.start() - 1 : m.start()] if m.start() else ""
        if after == "-" or before == "-":
            continue
        if "-" in surname:
            continue
        sn = norm_ru(surname)
        if not surname[0].isupper() and not surname.isupper():
            continue
        for key in forms_by_key:
            if not _match_surname(sn, key):
                continue
            if not _initials_ok(initials, key):
                continue
            hits.append(
                {
                    "author_key": key,
                    "surface": m.group(0),
                    "start": m.start(),
                    "end": m.end(),
                    "confidence": "high",
                    "form": "initials",
                }
            )
            break

    body = norm_ru(raw)
    for key, surfaces in forms_by_key.items():
        for surface in sorted(surfaces, key=len, reverse=True):
            if len(surface) < 4:
                continue
            pos = 0
            while True:
                idx = body.find(surface, pos)
                if idx < 0:
                    break
                end = idx + len(surface)
                pos = idx + 1
                left_ok = idx == 0 or not body[idx - 1].isalnum()
                right_ok = end >= len(body) or not body[end].isalnum()
                if not (left_ok and right_ok):
                    continue
                if idx > 0 and raw[idx - 1] == "-":
                    continue
                if end < len(raw) and raw[end] == "-":
                    continue
                raw_ch = raw[idx] if idx < len(raw) else ""
                surf_raw = raw[idx:end]
                if not (raw_ch.isupper() or surf_raw.isupper()):
                    continue
                # Initials immediately before surname must match this author
                prec = _preceding_initials(raw, idx)
                if prec is not None and not _initials_ok(prec, key):
                    continue
                near = _nearby_tokens(raw, idx, end, radius=3)
                matched_first = any(_first_name_ok(tok, key) for tok in near)
                foreign_first = False
                for tok in near:
                    if any(_first_name_ok(tok, k2) for k2 in forms_by_key if k2 != key):
                        if not _first_name_ok(tok, key):
                            foreign_first = True
                if foreign_first and not matched_first:
                    continue
                hits.append(
                    {
                        "author_key": key,
                        "surface": surf_raw,
                        "start": idx,
                        "end": end,
                        "confidence": "high" if matched_first else "medium",
                        "form": "surname",
                    }
                )

    filtered: list[dict[str, Any]] = []
    for h in hits:
        if h.get("form") == "initials":
            filtered.append(h)
            continue
        key = h["author_key"]
        pre = raw[: h["start"]].rstrip()
        if re.search(r"(?:[A-ZА-ЯЁ]\.\s*)+$", pre):
            filtered.append(h)
            continue
        mpre = re.search(r"([A-ZА-ЯЁ][a-zа-яёA-ZА-ЯЁ\-]{2,40})$", pre)
        if mpre:
            given = norm_ru(mpre.group(1))
            # Capitalized token before surname that is not this author's name
            # → reject («Игнатий Некрасов»). Genre words etc. may also reject
            # rare false cases; initials form is handled separately.
            if not _first_name_ok(given, key) and len(given) >= 4:
                continue
        filtered.append(h)

    filtered.sort(
        key=lambda h: (h["start"], -(h["end"] - h["start"]), 0 if h["confidence"] == "high" else 1)
    )
    out: list[dict[str, Any]] = []
    last_end = -1
    for h in filtered:
        if h["start"] < last_end:
            continue
        out.append(h)
        last_end = h["end"]
    return out


def detect_author_from_text_header(raw: str, *, max_lines: int = 10) -> str | None:
    """Find exactly one author FIO in the first non-empty lines."""
    if not raw:
        return None
    lines: list[str] = []
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        lines.append(s)
        if len(lines) >= max_lines:
            break
    head_n = norm_ru("\n".join(lines))
    if not head_n:
        return None

    table = author_name_table()
    matches: set[str] = set()

    for key, info in table.items():
        firsts = [norm_ru(x) for x in (info.get("first") or [])]
        pats = [norm_ru(x) for x in (info.get("patronymic") or [])]
        surnames = sorted(_surname_forms(key), key=len, reverse=True)
        initials = [norm_ru(x) for x in (info.get("initials") or [])]

        for sn in surnames:
            for fn in firsts:
                if pats:
                    for pn in pats:
                        if re.search(
                            rf"(?<![0-9a-zа-я]){re.escape(fn)}\s+{re.escape(pn)}\s+{re.escape(sn)}(?![0-9a-zа-я])",
                            head_n,
                        ):
                            matches.add(key)
                if re.search(
                    rf"(?<![0-9a-zа-я]){re.escape(fn)}\s+{re.escape(sn)}(?![0-9a-zа-я])",
                    head_n,
                ):
                    matches.add(key)
                if re.search(
                    rf"(?<![0-9a-zа-я]){re.escape(sn)}\s+{re.escape(fn)}(?![0-9a-zа-я])",
                    head_n,
                ):
                    matches.add(key)
            for ini in initials:
                letters = re.findall(r"[a-zа-яё]", ini.replace(" ", ""))
                if len(letters) < 1:
                    continue
                init_pat = r"\s*".join(re.escape(l) + r"\.?" for l in letters)
                if re.search(
                    rf"(?<![0-9a-zа-я]){init_pat}\s*{re.escape(sn)}(?![0-9a-zа-я])",
                    head_n,
                ):
                    matches.add(key)
                if re.search(
                    rf"(?<![0-9a-zа-я]){re.escape(sn)}\s*{init_pat}(?![0-9a-zа-я])",
                    head_n,
                ):
                    matches.add(key)

    if len(matches) == 1:
        return next(iter(matches))
    return None
