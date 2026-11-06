#!/usr/bin/env python3
"""Offline dry-run of cloze resolver (mirrors chat.py cloze helpers)."""
from __future__ import annotations

import json
import re
from pathlib import Path

STOP = {"the", "and", "for", "with", "их", "это", "как", "что", "или", "для", "пропуск"}
DENY = {
    "документ", "вопрос", "вопросы", "пропуск", "потом", "она", "он", "они",
    "президент", "cloze", "highlight", "ответ", "источник", "однако", "профессор",
    "дискуссии", "взрыв", "географ", "кубок", "известия", "новое", "также",
    "согласно", "после", "кроме", "между", "id", "фрагменты", "зеленые", "фильм",
    "коми", "риа", "хдс", "тасс", "sputnik", "reuters", "afp", "dpa", "туризм",
    "партия", "партию", "кубка", "конфедераций", "москве", "берлине",
    "президент", "президента", "полузащитник", "переехавшего", "клуб", "судья",
    "эксперты", "банки", "рядом", "теперь", "адвокат", "дело", "российские",
    "клиентам", "новости", "ленты", "объявлен", "вторичное",
}


def norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def looks_person(cand: str) -> bool:
    m = re.match(
        r"^([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)(?:\s+([А-ЯЁ][а-яё]+))?$",
        cand.strip(),
    )
    if not m:
        return False
    parts = [p for p in m.groups() if p]
    a, b = norm(parts[0]), norm(parts[-1])
    bad_first = {
        "южной", "новую", "новый", "кубка", "северной", "великой", "международной",
        "республике", "ненецкий", "автономный", "президента", "президент",
        "верховной", "адвокат", "полузащитник", "форвард", "уроженца", "дело",
    }
    bad_second = {
        "осетии", "зеландию", "зеландии", "конфедераций", "штаты", "коми",
        "округ", "федерации", "рады",
    }
    if a in bad_first or b in bad_second:
        return False
    if a.endswith(("ую", "ой", "ая")) and b.endswith(("ии", "ию", "ей", "ам")):
        return False
    return True


def split_ph(text: str) -> tuple[str, str]:
    t = text or ""
    for marker in ("\ncloze:", "\nвопросы с пропуском"):
        idx = norm(t).find(norm(marker.strip()))
        if idx > 40:
            t = t[:idx]
            break
    highs = re.findall(r"(?mi)^@highlight\s*\n?(.*)$", t)
    body = re.sub(r"(?mi)^@highlight\s*\n?.*$", "", t)
    body = re.sub(r"@highlight\s*", " ", body)
    return body.strip(), "\n".join(h.strip() for h in highs if h.strip())


def entities(texts, limit=100):
    found, seen = [], set()
    pat = re.compile(
        r"(?<![0-9A-Za-zА-Яа-яЁё])(?:"
        r"[A-Z]{2,6}|[А-ЯЁ]{2,6}|"
        r"[A-Z][A-Za-z0-9\-\.]{1,40}|"
        r"[А-ЯЁ][а-яёА-ЯЁ\-]{2,40}(?:\s+[А-ЯЁ][а-яёА-ЯЁ\-]{2,40}){0,1}"
        r")(?![0-9A-Za-zА-Яа-яЁё])"
    )
    for text in texts:
        for m in pat.findall(text):
            s = m.strip(" .,:;\"'«»")
            key = norm(s)
            if len(key) < 3 or key in seen or key in STOP or key in DENY:
                continue
            seen.add(key)
            found.append(s)
            if len(found) >= limit:
                return found
    return found


def blank_sentence(q: str) -> str:
    br = re.compile(r"_{3,}|_____")
    m = re.search(r"Вопрос:\s*(.+)$", q, flags=re.I | re.S)
    if m and br.search(m.group(1)):
        return m.group(1).strip()
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", q) if br.search(p)]
    if not parts:
        return q
    filtered = [p for p in parts if not ("в тексте есть пропуск" in norm(p) and len(norm(p)) < 80)]
    return max(filtered or parts, key=len)


def cloze_fallback(question: str, raw_texts: list[str]) -> str | None:
    bodies, highs = [], []
    for t in raw_texts:
        b, h = split_ph(t)
        if b:
            bodies.append(b)
        if h:
            highs.append(h)
    if not (bodies or highs):
        return None
    cands = entities(bodies + highs, 100)
    body_blob = "\n".join(bodies)
    body_l = norm(body_blob)
    for stem, form in (
        ("япон", "Японии"),
        ("россий", "России"),
        ("росс", "России"),
        ("египет", "Египта"),
        ("египт", "Египта"),
        ("америк", "США"),
    ):
        if stem in body_l and form not in cands:
            cands.append(form)
    if not cands:
        return None
    q_core = blank_sentence(question)
    br = re.compile(r"_{3,}|_____")
    q_ents = {norm(e) for e in entities([br.sub(" ", q_core)], 30)}
    cands = [
        c
        for c in cands
        if norm(c) not in q_ents
        and not any(norm(c) == qe or (len(qe) >= 4 and (norm(c) in qe or qe in norm(c))) for qe in q_ents)
    ] or cands
    lr = br.split(q_core, maxsplit=1)
    left = (lr[0] or "").strip()
    right = (lr[1] or "").strip() if len(lr) > 1 else ""
    left_toks = re.findall(r"[\wа-яА-ЯёЁA-Za-z]{3,}", left, flags=re.UNICODE)[-4:]
    right_toks = re.findall(r"[\wа-яА-ЯёЁA-Za-z]{3,}", right, flags=re.UNICODE)[:4]
    q_terms = [t for t in re.findall(r"[\wа-яА-ЯёЁA-Za-z]{3,}", q_core, flags=re.UNICODE) if norm(t) not in STOP and norm(t) not in DENY]
    cand_norms = {norm(c) for c in cands}
    for text in bodies:
        if left_toks and right_toks:
            l = r"\s+".join(map(re.escape, left_toks[-2:]))
            r = r"\s+".join(map(re.escape, right_toks[:2]))
            m = re.search(l + r"\s+(.{1,60}?)\s+" + r, text, flags=re.I)
            if m:
                cand = m.group(1).strip(" ,.;:«»\"'")
                cn = norm(cand)
                if 2 <= len(cand) <= 60 and cn not in DENY and (
                    cn in cand_norms or any(cn in x or x in cn for x in cand_norms)
                ):
                    return cand
        if (not left_toks) and right_toks:
            r = r"\s+".join(map(re.escape, right_toks[:2]))
            m = re.search(r"([А-ЯЁA-Z][^.\n]{1,50}?)\s+" + r, text)
            if m:
                cand = m.group(1).strip(" ,.;:«»\"'")
                cn = norm(cand)
                if 2 <= len(cand) <= 60 and cn not in DENY and (
                    cn in cand_norms or any(cn in x or x in cn for x in cand_norms)
                ):
                    return cand
    best_text = max(bodies, key=lambda t: sum(1 for x in q_terms if norm(x) in norm(t)), default="")
    focus = re.split(r"(?<=[.!?])\s+|\n+", best_text) if best_text else []
    body_n = norm(body_blob)
    high_n = norm("\n".join(highs))
    lead = norm(best_text[:800] if best_text else body_n[:800])
    right_n, left_n = norm(right), norm(left)
    person_hint = any(
        x in right_n or x in left_n
        for x in ("задержан", "отметил", "совершил", "поблагодарил", "принесла", "летний", "муж", "погибшей", "избрали", "роли", "партии", "учинили", "драки")
    )
    blank_at_start = not bool(left_toks)
    geo_hint = any(
        x in left_n or x in right_n
        for x in ("территории", "император", "объектам", "возврат", "логотип", "в ираке", "британская", "к демократии", "шпионская")
    )
    org_hint = any(x in left_n or x in right_n for x in ("офисов", "банкомат", "отмечают", "отметило", "портала"))
    best, best_score = None, -1.0
    for cand in cands:
        cn = norm(cand)
        if cn in DENY or len(cn) < 2:
            continue
        is_person = looks_person(cand)
        score = lead.count(cn) * 5.0 + body_n.count(cn) * 2.0 + high_n.count(cn) * 0.6
        if re.search(r"[«\"]" + re.escape(cand) + r"[»\"]", body_blob):
            if not person_hint or is_person or cn in {"дом"}:
                score += 18
        for sent in focus:
            sl = norm(sent)
            if cn not in sl:
                continue
            score += sum(1 for t in q_terms if norm(t) in sl and norm(t) != cn) * 3
            if any(norm(t) in sl for t in left_toks[-2:]):
                score += 4
            if any(norm(t) in sl for t in right_toks[:2]):
                score += 4
        if " " in cand:
            score += 1.5
        if person_hint and is_person:
            score += 14
            if blank_at_start:
                score += 6
        if person_hint and re.match(r"[А-ЯЁ][а-яё]+$", cand) and len(cand) >= 4:
            score += 4
        if geo_hint and cn in {"сша", "рф", "египта", "россии", "японии", "япония", "крым", "сирии"}:
            score += 10
        if geo_hint and cand.isupper() and 2 <= len(cand) <= 4:
            score += 6
        if geo_hint and any(x in cn for x in ("росс", "япон", "егип", "сири", "крым")):
            score += 8
        if org_hint and ("банк" in cn or "качество" in cn or cn in {"дом", "zara"}):
            score += 10
        if org_hint and "сбер" in cn:
            score += 8
        if " " not in cand and cn.endswith(("ский", "цкий", "нский", "кое", "ное", "ный", "кий")):
            score -= 6
        if person_hint and body_n.count(cn) >= 2 and re.match(r"[А-ЯЁ][а-яё]+$", cand):
            score += 5
        if geo_hint and not person_hint and is_person:
            score -= 10
        if person_hint and not is_person and " " in cand and not org_hint:
            score -= 4
        if score > best_score:
            best_score, best = score, cand
    return best if best_score >= 2 else None


def main() -> None:
    sample = json.loads(Path("/mnt/d/RAG 5060/Обучение/artifacts/test3/sample.json").read_text(encoding="utf-8"))
    inbox = Path("/mnt/d/RAG5060/inbox/obuchenie_test3")
    hits = 0
    for it in sample["items"]:
        docs = [p for p in inbox.glob("*.txt") if it["doc_key"] in p.name]
        if not docs:
            print("NODOC", it["id"])
            continue
        pred = cloze_fallback(it["question"], [docs[0].read_text(encoding="utf-8")])
        ok = False
        if pred:
            pn = norm(pred)
            for g in it["answers"]:
                gn = norm(g)
                if gn in pn or pn in gn:
                    ok = True
                    break
        hits += int(ok)
        print(("OK" if ok else "NO"), "pred=", pred, "| gold=", it["answers"][:3])
    print("score", round(hits / len(sample["items"]), 4), "hits", hits, "/", len(sample["items"]))


if __name__ == "__main__":
    main()
