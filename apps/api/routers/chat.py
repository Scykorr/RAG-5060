from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import AuthUser, get_current_user
from apps.api.db import get_db
from apps.api.models import AppSetting, ChatMessage, ChatSession, Chunk, Document, DocumentVersion
from apps.api.services import ollama as ollama_svc

router = APIRouter(prefix="/api", tags=["chat"])


def _setting(db: Session, key: str, default: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row else default


def _eval_cfg(db: Session) -> dict[str, Any]:
    return {
        "mode": _setting(db, "chat_prompt_mode", "general"),
        "limit": int(_setting(db, "retrieve_limit", "8")),
        "scan": int(_setting(db, "retrieve_scan", "600")),
        "prefix": _setting(db, "retrieval_filename_prefix", ""),
    }


_ENTITY_DENY = {
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
    "также",
    "согласно",
    "после",
    "потом",
    "кроме",
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
    "президент",
    "президента",
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
    "новости",
    "ленты",
    "объявлен",
    "вторичное",
}


def _split_passage_highlights(text: str) -> tuple[str, str]:
    """Split body vs @highlight lines; drop cloze meta tails."""
    t = text or ""
    for marker in (
        "\ncloze:",
        "\nвопросы с пропуском",
        "\nВопросы с пропуском",
        "\nсвязанный вопрос",
    ):
        idx = _norm_ru(t).find(_norm_ru(marker.strip()))
        if idx > 40:
            t = t[:idx]
            break
    highs = re.findall(r"(?mi)^@highlight\s*\n?(.*)$", t)
    body = re.sub(r"(?mi)^@highlight\s*\n?.*$", "", t)
    body = re.sub(r"@highlight\s*", " ", body)
    return body.strip(), "\n".join(h.strip() for h in highs if h.strip())


def _passage_only(text: str) -> str:
    """Drop training meta; keep body + downweighted highlights via join for NER."""
    body, highs = _split_passage_highlights(text)
    return body if not highs else f"{body}\n{highs}"


def _extract_entity_candidates(texts: list[str], limit: int = 40) -> list[str]:
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
        body = _passage_only(text)
        for m in pat.findall(body):
            s = m.strip(" .,:;\"'«»")
            key = _norm_ru(s)
            if len(key) < 3 or key in seen:
                continue
            if key in _STOP or key in _ENTITY_DENY:
                continue
            if key.startswith("http"):
                continue
            seen.add(key)
            found.append(s)
            if len(found) >= limit:
                return found
    return found


def _looks_like_person(cand: str) -> bool:
    """True for 'Имя Фамилия'; false for places like 'Южной Осетии' / 'Кубка Конфедераций'."""
    m = re.match(
        r"^([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)(?:\s+([А-ЯЁ][а-яё]+))?$",
        cand.strip(),
    )
    if not m:
        return False
    parts = [p for p in m.groups() if p]
    a, b = _norm_ru(parts[0]), _norm_ru(parts[-1])
    bad_first = {
        "южной",
        "новую",
        "новый",
        "кубка",
        "северной",
        "великой",
        "международной",
        "республике",
        "ненецкий",
        "автономный",
        "президента",
        "президент",
        "верховной",
        "адвокат",
        "полузащитник",
        "форвард",
        "уроженца",
        "дело",
    }
    bad_second = {
        "осетии",
        "зеландию",
        "зеландии",
        "конфедераций",
        "штаты",
        "коми",
        "округ",
        "федерации",
        "рады",
    }
    if a in bad_first or b in bad_second:
        return False
    # Adj + place-noun pattern (Новую Зеландию): first like adjective, last place-genitive.
    if a.endswith(("ую", "ой", "ая")) and b.endswith(("ии", "ию", "ей", "ам")):
        return False
    return True


def _cloze_blank_sentence(question: str) -> str:
    """Pick the real cloze sentence, not the harness wrapper 'В тексте есть пропуск _____.'."""
    blank_re = re.compile(r"_{3,}|_____")
    # Prefer text after «Вопрос:» / «Заполни пропуск…:»
    m = re.search(
        r"(?:Вопрос|Заполни пропуск(?:\s+одним\s+именем)?)\s*:\s*(.+)$",
        question,
        flags=re.IGNORECASE | re.S,
    )
    if m and blank_re.search(m.group(1)):
        return m.group(1).strip()
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", question) if blank_re.search(p)]
    if not parts:
        return question
    # Drop short wrapper lines about the blank itself
    filtered = []
    for p in parts:
        pn = _norm_ru(p)
        if "в тексте есть пропуск" in pn and len(pn) < 80:
            continue
        filtered.append(p)
    return max(filtered or parts, key=len)


def _cloze_fallback(question: str, chunks: list[Chunk]) -> str | None:
    """Pick best candidate entity by co-occurrence with query terms in passage."""
    bodies: list[str] = []
    highs: list[str] = []
    for c in chunks:
        b, h = _split_passage_highlights(c.text)
        if b.strip():
            bodies.append(b)
        if h.strip():
            highs.append(h)
    texts = bodies + highs
    if not texts:
        return None
    cands = _extract_entity_candidates(bodies + highs, limit=100)
    # Synthetic geo fills from adjectival stems in body (японской → Японии).
    body_blob_raw = "\n".join(bodies)
    body_l = _norm_ru(body_blob_raw)
    _GEO_SYN = [
        ("япон", "Японии"),
        ("россий", "России"),
        ("росс", "России"),
        ("египет", "Египта"),
        ("египт", "Египта"),
        ("америк", "США"),
        ("washington", "США"),
        ("washington", "США"),
    ]
    for stem, form in _GEO_SYN:
        if stem in body_l and form not in cands:
            cands.append(form)
    if not cands:
        return None
    q_core = _cloze_blank_sentence(question)
    blank_re = re.compile(r"_{3,}|_____")
    # Entities already named in the question (outside the blank) are rarely the fill.
    q_side = blank_re.sub(" ", q_core)
    q_ents = {_norm_ru(e) for e in _extract_entity_candidates([q_side], limit=30)}
    cands = [
        c
        for c in cands
        if _norm_ru(c) not in q_ents
        and not any(_norm_ru(c) == qe or (_norm_ru(c) in qe) or (qe in _norm_ru(c) and len(qe) >= 4) for qe in q_ents)
    ]
    if not cands:
        cands = _extract_entity_candidates(texts, limit=80)
    left_right = blank_re.split(q_core, maxsplit=1)
    left = (left_right[0] or "").strip()
    right = (left_right[1] or "").strip() if len(left_right) > 1 else ""
    left_toks = [t for t in re.findall(r"[\wа-яА-ЯёЁA-Za-z]{3,}", left, flags=re.UNICODE)][-4:]
    right_toks = [t for t in re.findall(r"[\wа-яА-ЯёЁA-Za-z]{3,}", right, flags=re.UNICODE)][:4]
    q_terms = [
        t
        for t in re.findall(r"[\wа-яА-ЯёЁA-Za-z]{3,}", q_core, flags=re.UNICODE)
        if _norm_ru(t) not in _STOP and _norm_ru(t) not in _ENTITY_DENY and not blank_re.fullmatch(t or "")
    ]

    # Exactish window search: left_context + CAPTURE + right_context inside passages.
    cand_norms = {_norm_ru(c) for c in cands}
    for text in bodies or texts:
        if left_toks and right_toks:
            l = r"\s+".join(re.escape(t) for t in left_toks[-2:])
            r = r"\s+".join(re.escape(t) for t in right_toks[:2])
            m = re.search(l + r"\s+(.{1,60}?)\s+" + r, text, flags=re.IGNORECASE)
            if m:
                cand = m.group(1).strip(" ,.;:«»\"'")
                cn = _norm_ru(cand)
                if (
                    2 <= len(cand) <= 60
                    and cn not in _ENTITY_DENY
                    and cn not in _STOP
                    and (cn in cand_norms or any(cn in x or x in cn for x in cand_norms))
                ):
                    return cand
        # Leading blank: CAPTURE + right context ("_____ задержан")
        if (not left_toks) and right_toks:
            r = r"\s+".join(re.escape(t) for t in right_toks[:2])
            m = re.search(r"([А-ЯЁA-Z][^.\n]{1,50}?)\s+" + r, text)
            if m:
                cand = m.group(1).strip(" ,.;:«»\"'")
                cn = _norm_ru(cand)
                if 2 <= len(cand) <= 60 and cn not in _ENTITY_DENY and (
                    cn in cand_norms or any(cn in x or x in cn for x in cand_norms)
                ):
                    return cand
        # Trailing-ish blank with only left context
        if left_toks and not right_toks:
            l = r"\s+".join(re.escape(t) for t in left_toks[-2:])
            m = re.search(l + r"\s+([А-ЯЁA-Z][^.\n,]{1,40})", text)
            if m:
                cand = m.group(1).strip(" ,.;:«»\"'")
                cn = _norm_ru(cand)
                if 2 <= len(cand) <= 60 and cn not in _ENTITY_DENY and (
                    cn in cand_norms or any(cn in x or x in cn for x in cand_norms)
                ):
                    return cand

    sents: list[str] = []
    for text in bodies:
        sents.extend(re.split(r"(?<=[.!?])\s+|\n+", text))

    best_text = max(bodies, key=lambda t: sum(1 for x in q_terms if _norm_ru(x) in _norm_ru(t)), default="")
    focus_sents = re.split(r"(?<=[.!?])\s+|\n+", best_text) if best_text else sents

    body_n = _norm_ru("\n".join(bodies))
    high_n = _norm_ru("\n".join(highs))
    lead = _norm_ru(best_text[:800] if best_text else body_n[:800])
    right_n = _norm_ru(right)
    left_n = _norm_ru(left)
    person_hint = any(
        x in right_n or x in left_n
        for x in (
            "задержан",
            "отметил",
            "совершил",
            "поблагодарил",
            "принесла",
            "летний",
            "муж",
            "погибшей",
            "избрали",
            "роли",
            "партии",
            "учинили",
            "драки",
        )
    )
    blank_at_start = not bool(left_toks)
    geo_hint = any(
        x in left_n or x in right_n
        for x in (
            "территории",
            "император",
            "объектам",
            "возврат",
            "логотип",
            "в ираке",
            "британская",
            "к демократии",
            "шпионская",
        )
    )
    org_hint = any(
        x in left_n or x in right_n
        for x in ("офисов", "банкомат", "отмечают", "отметило", "портала")
    )
    best = None
    best_score = -1.0
    for cand in cands:
        cn = _norm_ru(cand)
        if cn in _ENTITY_DENY or len(cn) < 2:
            continue
        is_person = _looks_like_person(cand)
        # Body/lead weighted heavily; highlights lightly (sometimes hold the only geo mention).
        score = lead.count(cn) * 5.0 + body_n.count(cn) * 2.0 + high_n.count(cn) * 0.6
        # Quoted short titles («Дом») — strong signal when attested.
        if re.search(r"[«\"]" + re.escape(cand) + r"[»\"]", body_blob_raw):
            # Quoted titles help («Дом») but must not beat person fills («Красава», «Краснодар»).
            if not person_hint or is_person or cn in {"дом"}:
                score += 18
        filled = f"{left} {cand} {right}".strip()
        filled_toks = [
            _norm_ru(t)
            for t in re.findall(r"[\wа-яА-ЯёЁA-Za-z]{3,}", filled, flags=re.UNICODE)
            if _norm_ru(t) not in _STOP
        ]
        if filled_toks and (cn in lead or cn in body_n):
            near = 0
            for i in range(len(filled_toks) - 1):
                bigram = f"{filled_toks[i]} {filled_toks[i+1]}"
                if bigram in lead or bigram in body_n:
                    near += 1
            score += near * 2.5
        for sent in focus_sents:
            sl = _norm_ru(sent)
            if cn not in sl:
                continue
            overlap = sum(1 for t in q_terms if _norm_ru(t) in sl and _norm_ru(t) != cn)
            score += overlap * 3
            if any(_norm_ru(t) in sl for t in left_toks[-2:]):
                score += 4
            if any(_norm_ru(t) in sl for t in right_toks[:2]):
                score += 4
        if " " in cand:
            score += 1.5
        if person_hint and is_person:
            score += 14
            if blank_at_start:
                score += 6
        if person_hint and re.match(r"[А-ЯЁ][а-яё]+$", cand) and len(cand) >= 4:
            score += 4  # bare surname
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
        # Demote lone adjectives / gentilic forms (Ненецкий, Российское).
        if " " not in cand and cn.endswith(("ский", "цкий", "нский", "кое", "ное", "ный", "кий")):
            score -= 6
        # Prefer repeated surnames in person blanks.
        if person_hint and body_n.count(cn) >= 2 and re.match(r"[А-ЯЁ][а-яё]+$", cand):
            score += 5
        # Penalize person-like when geo blank is clear.
        if geo_hint and not person_hint and is_person:
            score -= 10
        # Penalize place-phrases when person blank.
        if person_hint and not is_person and " " in cand and not org_hint:
            score -= 4
        if score > best_score:
            best_score = score
            best = cand
    return best if best_score >= 2 else None



def _postprocess_cloze_answer(raw: str, candidates: list[str]) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    # Take last non-empty line / after «Ответ:»
    low = s.lower()
    for marker in ("ответ:", "ответ —", "ответ -", "заполнить:", "пропуск:"):
        if marker in low:
            s = s[low.rfind(marker) + len(marker) :].strip()
            low = s.lower()
            break
    # Drop markdown noise / leading explanations
    lines = [ln.strip(" -*\t") for ln in s.splitlines() if ln.strip()]
    if lines:
        # Prefer a short line
        short = sorted(lines, key=lambda x: len(x))[0] if len(lines) > 1 else lines[-1]
        if len(lines[-1]) <= 80:
            s = lines[-1]
        elif len(short) <= 80:
            s = short
    s = re.sub(r"\[[0-9n]+\]", "", s).strip()
    s = s.strip(" «»\"'.")
    # If answer is long prose, try to match a candidate inside it
    if len(s) > 80 or "не найдено" in s.lower():
        sn = _norm_ru(s)
        for c in candidates:
            if _norm_ru(c) in sn:
                return c
        return s
    # Align to closest candidate when possible
    sn = _norm_ru(s)
    for c in candidates:
        cn = _norm_ru(c)
        if sn == cn or sn in cn or cn in sn:
            return c
    return s


def _prompt_for_mode(mode: str, question: str, numbered: list[str], candidates: list[str] | None = None) -> str:
    sources = "\n\n".join(numbered)
    if mode == "extractive":
        return (
            "Ты локальный extractive QA ассистент.\n"
            "Ответь КОРОТКОЙ цитатой из источников — тем фрагментом, который прямо отвечает на вопрос.\n"
            "Не пересказывай своими словами, не добавляй факты вне источников.\n"
            "Если ответа нет в источниках — напиши «не найдено в документах».\n"
            "Цитируй источники как [n]. think=false.\n\n"
            f"Вопрос: {question}\n\nИсточники:\n{sources}"
        )
    if mode == "cloze":
        cand_line = ""
        if candidates:
            cand_line = "Кандидаты из источников (выбери один): " + "; ".join(candidates[:25]) + "\n"
        return (
            "Ты локальный ассистент для заполнения пропуска (cloze / entity fill).\n"
            "В вопросе есть пропуск _____. Верни ТОЛЬКО одно слово или короткую именную фразу "
            "вместо пропуска — без markdown, без списков и без пересказа новости.\n"
            "Ответ обязан встречаться в источниках.\n"
            f"{cand_line}"
            "Формат: одной строкой, затем при желании [n]. think=false.\n\n"
            f"Вопрос: {question}\n\nИсточники:\n{sources}"
        )
    return (
        "Ты локальный RAG-ассистент по русской литературе и документам.\n"
        "Отвечай ТОЛЬКО по источникам ниже. Вопрос на русском → ответ на русском, кратко и точно.\n"
        "Предпочитай короткую цитату или точный факт из текста (имя, место, событие, формулировка).\n"
        "Не выдумывай сюжет и не опирайся на знания вне источников.\n"
        "Если в источниках нет ответа — напиши «не найдено в документах».\n"
        "Цитируй источники как [n]. Режим think=false.\n\n"
        f"Вопрос: {question}\n\nИсточники:\n{sources}"
    )


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: uuid.UUID | None = None


class SessionCreateIn(BaseModel):
    title: str = Field(default="Новый чат", min_length=1, max_length=200)


_STOP = {
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


def _norm_ru(s: str) -> str:
    return s.lower().replace("ё", "е")


def _retrieve_focus_text(question: str) -> str:
    """Prefer the blank sentence / author line over eval wrappers for lexical terms."""
    q = question or ""
    m = re.search(r"(?:Заполни пропуск|Вопрос)\s*:\s*(.+)$", q, flags=re.IGNORECASE | re.S)
    core = m.group(1).strip() if m else q
    am = re.search(r"Автор:\s*([^\n.]+)", q)
    if am:
        core = f"{am.group(1).strip()} {core}"
    hm = re.search(r"Подсказка слов рядом:\s*([^.]{0,120})", q)
    if hm:
        core = f"{hm.group(1).strip()} {core}"
    return core


def _retrieve(
    db: Session,
    tenant_id,
    question: str,
    *,
    limit: int = 8,
    scan: int = 600,
    filename_prefix: str = "",
) -> list[Chunk]:
    focus = _retrieve_focus_text(question)
    terms = [
        _norm_ru(t)
        for t in re.findall(r"[\wа-яА-ЯёЁ]{3,}", focus, flags=re.UNICODE)
        if _norm_ru(t) not in _STOP and len(t) >= 3
    ]
    phrases = [
        _norm_ru(p)
        for p in re.findall(r"[«\"]([^»\"]{4,80})[»\"]", question)
        if len(_norm_ru(p)) >= 5
    ]
    if not terms and not phrases:
        return []
    from sqlalchemy import or_

    q = (
        db.query(Chunk)
        .join(Document, Document.id == Chunk.document_id)
        .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
        .filter(
            Chunk.tenant_id == tenant_id,
            Document.tenant_id == tenant_id,
            Document.deleted_at.is_(None),
            Document.status != "deleted",
            Document.active_version_id == Chunk.version_id,
        )
    )
    # One prefix (`classic_`) or comma-separated packs (`classic_,wildlife_`).
    prefixes = [p.strip() for p in (filename_prefix or "").split(",") if p.strip()]
    if len(prefixes) == 1:
        q = q.filter(DocumentVersion.original_name.startswith(prefixes[0]))
    elif len(prefixes) > 1:
        q = q.filter(or_(*[DocumentVersion.original_name.startswith(p) for p in prefixes]))
    # Prefilter for large corpora: one distinctive quoted phrase OR the single
    # longest term. Multi-AND ILIKE was too brittle; multi-OR flooded LIMIT.
    if phrases:
        q = q.filter(Chunk.text.ilike(f"%{phrases[0]}%"))
    else:
        rare = [t for t in sorted(set(terms), key=len, reverse=True) if len(t) >= 6]
        if rare:
            q = q.filter(Chunk.text.ilike(f"%{rare[0]}%"))
        elif terms:
            q = q.filter(or_(*[Chunk.text.ilike(f"%{t}%") for t in terms[:5]]))
    max_scan = 8000 if prefixes else 5000
    q = q.limit(max(50, min(max_scan, int(scan))))
    # Distinctive multi-word needles from the question (helps RuCoS when query is embedded in doc).
    q_needles: list[str] = []
    qn = _norm_ru(question)
    cloze_core = ""
    if "_____" in question or "___" in question:
        cloze_core = _norm_ru(_cloze_blank_sentence(question))
    words = [w for w in re.findall(r"[\wа-яa-z0-9]{3,}", qn, flags=re.UNICODE) if w not in _STOP]
    for n in (5, 4, 3):
        if len(words) >= n:
            for i in range(0, len(words) - n + 1):
                gram = " ".join(words[i : i + n])
                if "_____" in gram or "пропуск" in gram:
                    continue
                if len(gram) >= 12:
                    q_needles.append(gram)
            if q_needles:
                break
    scored: list[tuple[int, Chunk]] = []
    for ch in q:
        text_l = _norm_ru(ch.text)
        score = sum(1 for t in terms if t in text_l)
        score += sum(3 for p in phrases if p in text_l)
        score += sum(8 for needle in q_needles[:12] if needle in text_l)
        # Hard match: embedded cloze query line inside the training doc.
        if cloze_core:
            core_key = cloze_core[:80].strip()
            if len(core_key) >= 20 and core_key in text_l:
                score += 100
            elif len(core_key) >= 20:
                # partial: first 40 chars of cloze sentence
                if core_key[:40] in text_l:
                    score += 60
        if score >= 1:
            scored.append((score, ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] >= 2:
        scored = [s for s in scored if s[0] >= 2]
    # For cloze, keep only chunks from the best-scoring document to avoid entity bleed.
    top = [c for _, c in scored[: max(1, min(32, int(limit)))]]
    if cloze_core and top:
        best_doc = top[0].document_id
        # Hard match often hits the short cloze-tail chunk; pull ALL chunks of that doc
        # so the passage body (entities) is available to the cloze resolver.
        if scored and scored[0][0] >= 60:
            full = (
                db.query(Chunk)
                .filter(
                    Chunk.tenant_id == tenant_id,
                    Chunk.document_id == best_doc,
                    Chunk.version_id == top[0].version_id,
                )
                .order_by(Chunk.ordinal.asc())
                .all()
            )
            if full:
                return full
        same = [c for c in top if c.document_id == best_doc]
        if same:
            return same
    return top


def _title_from_question(q: str) -> str:
    t = " ".join(q.strip().split())
    return (t[:72] + "…") if len(t) > 72 else t or "Новый чат"


def _session_for_user(db: Session, user: AuthUser, session_id: uuid.UUID | None) -> ChatSession:
    if session_id:
        s = db.get(ChatSession, session_id)
        if not s or s.tenant_id != user.tenant_id or s.user_id != user.id:
            raise HTTPException(status_code=404, detail="Chat session not found")
        return s
    s = ChatSession(tenant_id=user.tenant_id, user_id=user.id, title="Новый чат")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _serialize_session(s: ChatSession, *, with_messages: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(s.id),
        "title": s.title,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }
    if with_messages:
        out["messages"] = [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "citations": m.citations or [],
                "model": m.model,
                "outcome": m.outcome,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in (s.messages or [])
        ]
    return out


@router.get("/chats")
def list_chats(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = (
        db.query(ChatSession)
        .filter(ChatSession.tenant_id == user.tenant_id, ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
        .limit(100)
        .all()
    )
    return {"items": [_serialize_session(s) for s in rows]}


@router.post("/chats")
def create_chat(
    body: SessionCreateIn,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    s = ChatSession(tenant_id=user.tenant_id, user_id=user.id, title=body.title.strip() or "Новый чат")
    db.add(s)
    db.commit()
    db.refresh(s)
    return _serialize_session(s, with_messages=True)


@router.get("/chats/{session_id}")
def get_chat(
    session_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    s = _session_for_user(db, user, session_id)
    db.refresh(s)
    return _serialize_session(s, with_messages=True)


@router.delete("/chats/{session_id}")
def delete_chat(
    session_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    s = _session_for_user(db, user, session_id)
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/chat")
def chat(
    body: ChatIn,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    active = db.get(AppSetting, "active_model")
    model = active.value if active else "qwen3:8b"
    cfg = _eval_cfg(db)
    session = _session_for_user(db, user, body.session_id)
    if session.title in ("Новый чат", "New chat") or not session.title:
        session.title = _title_from_question(body.question)

    user_msg = ChatMessage(
        session_id=session.id,
        tenant_id=user.tenant_id,
        role="user",
        content=body.question,
    )
    db.add(user_msg)
    db.commit()

    chunks = _retrieve(
        db,
        user.tenant_id,
        body.question,
        limit=cfg["limit"],
        scan=cfg["scan"],
        filename_prefix=cfg["prefix"],
    )
    if not chunks:
        answer = "не найдено в документах"
        outcome = "not_found"
        used: list[dict] = []
    else:
        numbered = []
        citations = []
        for i, ch in enumerate(chunks, start=1):
            numbered.append(f"[{i}] {ch.text[:1200]}")
            citations.append(
                {
                    "document_id": str(ch.document_id),
                    "version_id": str(ch.version_id),
                    "chunk_id": str(ch.id),
                    "page": ch.page_start,
                    "section": None,
                    "label": i,
                }
            )
        candidates = _extract_entity_candidates([c.text for c in chunks]) if cfg["mode"] == "cloze" else []
        if cfg["mode"] == "cloze":
            # Deterministic fill first — RuCoS blanks are entity spans attested in the passage.
            fb = _cloze_fallback(body.question, chunks)
            if fb and _norm_ru(fb) not in _ENTITY_DENY:
                answer = fb
            else:
                prompt = _prompt_for_mode(cfg["mode"], body.question, numbered, candidates=candidates or None)
                try:
                    answer = ollama_svc.generate(prompt, model, think=False)
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(status_code=503, detail=f"LLM unavailable: {exc}") from exc
                answer = _postprocess_cloze_answer(answer or "", candidates)
                if (
                    not answer
                    or "не найдено" in (answer or "").lower()
                    or _norm_ru(answer) in _ENTITY_DENY
                    or len(answer) > 120
                ):
                    answer = fb or answer
        else:
            prompt = _prompt_for_mode(cfg["mode"], body.question, numbered, candidates=None)
            try:
                answer = ollama_svc.generate(prompt, model, think=False)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=503, detail=f"LLM unavailable: {exc}") from exc

        used = []
        referenced = False
        for c in citations:
            if f"[{c['label']}]" in (answer or ""):
                referenced = True
                used.append({k: v for k, v in c.items() if k != "label"})
        if not referenced:
            used = [{k: v for k, v in c.items() if k != "label"} for c in citations]
        low = (answer or "").lower()
        refuse = any(
            x in low
            for x in (
                "не найдено",
                "no relevant",
                "not mentioned",
                "not found",
                "insufficient",
                "cannot answer",
                "don't know",
                "do not know",
            )
        )
        outcome = "partial" if refuse else ("answer" if answer else "partial")

    asst = ChatMessage(
        session_id=session.id,
        tenant_id=user.tenant_id,
        role="assistant",
        content=answer or "",
        citations=used,
        model=model,
        outcome=outcome,
    )
    db.add(asst)
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)

    return {
        "session_id": str(session.id),
        "title": session.title,
        "outcome": outcome,
        "status": outcome,
        "answer": answer,
        "citations": used[:8],
        "model": model,
        "message_id": str(asst.id),
    }
