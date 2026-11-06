"""Local quality metrics — no network."""

from __future__ import annotations

import re


def norm_ru(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[«»\"'“”]", "", s)
    s = re.sub(r"[^\wа-яa-z0-9\s\-]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _stem_ru(s: str) -> str:
    s = norm_ru(s)
    for suf in (
        "ого",
        "ему",
        "ами",
        "ями",
        "ах",
        "ях",
        "ов",
        "ев",
        "ей",
        "ом",
        "ем",
        "ую",
        "ая",
        "ое",
        "ые",
        "ие",
        "у",
        "а",
        "я",
        "и",
        "ы",
        "е",
    ):
        if len(s) > len(suf) + 3 and s.endswith(suf):
            return s[: -len(suf)]
    return s


def contain_hit(answer: str, golds: list[str]) -> bool:
    a = norm_ru(answer)
    if not a:
        return False
    as_ = _stem_ru(a)
    for g in golds:
        ng = norm_ru(str(g))
        if not ng:
            continue
        if ng in a or a in ng:
            return True
        gs = _stem_ru(ng)
        if gs and (gs == as_ or gs in a or as_ in ng):
            return True
        gt = set(ng.split())
        at = set(a.split())
        if gt and len(gt & at) / len(gt) >= 0.8:
            return True
    return False


def level_from_score(score: float) -> str:
    if score >= 0.90:
        return "strong"
    if score >= 0.75:
        return "good"
    if score >= 0.55:
        return "ok"
    return "low"
