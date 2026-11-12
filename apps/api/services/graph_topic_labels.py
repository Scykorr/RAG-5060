"""Local labels for graph topics: c-TF-IDF terms, optional Ollama, overrides.

Offline. If the local model is down, callers keep the term label and the build
still succeeds. No external APIs.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Callable


def ctfidf(
    class_counts: dict[str, dict[str, int] | Counter],
    *,
    surfaces: dict[str, str] | None = None,
    top_n: int = 10,
    ban: set[str] | None = None,
    lang: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Class-based TF-IDF. Display term is the most common surface, not the stem."""
    ban_l = {str(x).casefold() for x in (ban or set())}
    surfaces = surfaces or {}
    n_classes = max(1, len(class_counts))
    df: Counter[str] = Counter()
    for counts in class_counts.values():
        for term, tf in counts.items():
            if int(tf) > 0:
                df[str(term)] += 1
    out: dict[str, list[dict[str, Any]]] = {}
    for cid, counts in class_counts.items():
        scored: list[dict[str, Any]] = []
        for term, tf in counts.items():
            stem = str(term)
            if int(tf) <= 0 or len(stem) < 4:
                continue
            if stem.casefold() in ban_l:
                continue
            # Drop terms that sit in almost every class (function-word residue).
            if n_classes >= 5 and df[stem] > 0.85 * n_classes:
                continue
            score = float(tf) * math.log((1.0 + n_classes) / (1.0 + df[stem]))
            if score <= 0:
                continue
            display = surfaces.get(stem) or stem
            scored.append(
                {
                    "stem": stem,
                    "term": display,
                    "score": round(score, 4),
                    "tf": int(tf),
                    "lang": lang,
                }
            )
        scored.sort(key=lambda r: (-float(r["score"]), str(r["stem"])))
        out[str(cid)] = scored[:top_n]
    return out


def default_label(terms: list[str], central_title: str | None, *, n_terms: int = 3) -> str:
    """2–4 characteristic terms and the central work title."""
    picked: list[str] = []
    for raw in terms:
        t = str(raw or "").strip()
        if not t:
            continue
        if t.casefold() in {p.casefold() for p in picked}:
            continue
        picked.append(t)
        if len(picked) >= max(2, min(4, n_terms)):
            break
    title = (central_title or "").strip()
    if len(title) > 64:
        title = title[:61].rstrip() + "…"
    head = ", ".join(picked[:4])
    if head and title:
        return f"{head} — {title}"
    if head:
        return head
    return title or "Тема"


def maybe_llm_label(
    *,
    enabled: bool,
    model: str,
    terms: list[str],
    titles: list[str],
    lang: str,
    cache_key: str,
    cache: dict[str, str],
    generate: Callable[[str, str], str] | None = None,
) -> str | None:
    """Short local-model label (≤5 words). Returns None when disabled or unavailable."""
    if not enabled:
        return None
    cached = cache.get(cache_key)
    if cached:
        return cached
    lang_name = {"ru": "русском", "en": "английском"}.get(lang or "", "языке большинства документов")
    prompt = (
        f"Дай короткое тематическое название (2–5 слов) на {lang_name}. "
        "Это предмет/тема корпуса, не стиль и не имя автора. "
        "Примеры хороших названий: «Кавказ и война», «Крестьянская жизнь», "
        "«Религия и непротивление». Без кавычек, без имён авторов, только название.\n"
        f"Характерные термины: {', '.join(terms[:10]) or '—'}\n"
        f"Центральные произведения: {'; '.join(titles[:5]) or '—'}\n"
    )

    def _default_generate(prompt_text: str, model_name: str) -> str:
        # Short timeout: topic labels must not block a full rebuild for minutes each.
        import httpx

        from apps.api.services import ollama as oll

        host = oll._host()  # noqa: SLF001 — same local Ollama endpoint
        payload = {
            "model": model_name,
            "prompt": prompt_text,
            "stream": False,
            "think": False,
            "options": {"num_predict": 48, "temperature": 0.2},
        }
        with httpx.Client(timeout=90.0) as client:
            r = client.post(f"{host}/api/generate", json=payload)
            r.raise_for_status()
            data = r.json() if r.content else {}
            text = str(data.get("response") or "")
            if not text.strip():
                # Some qwen3 builds put the short answer only in thinking.
                text = str(data.get("thinking") or "")
            return text

    fn = generate or _default_generate
    try:
        raw = fn(prompt, model) or ""
    except Exception:
        return None
    text = str(raw)
    # Drop chain-of-thought wrappers if the model ignored think=false.
    lower = text.lower()
    for tag in ("</think>", "</thinking>"):
        if tag in lower:
            idx = lower.rfind(tag)
            text = text[idx + len(tag) :]
            lower = text.lower()
            break
    line = ""
    for part in text.splitlines():
        cleaned = part.strip().strip(" \t\"'«»“”„#-")
        if not cleaned:
            continue
        if cleaned.lower().startswith(("think", "thinking", "рассужд")):
            continue
        line = cleaned
        break
    words = [w for w in line.split() if w]
    if not words:
        return None
    label = " ".join(words[:5])[:120]
    cache[cache_key] = label
    return label
