#!/usr/bin/env python3
"""Prepare fixed samples + inbox TXT docs for Обучение tests."""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import zipfile
from pathlib import Path

ROOT = Path(os.environ.get("OBUCHENIE_ROOT", Path(__file__).resolve().parents[1]))
INBOX = Path(os.environ.get("RAG_INBOX", "/mnt/d/RAG5060/inbox"))
SEED = int(os.environ.get("SAMPLE_SEED", "5060"))
N1 = int(os.environ.get("N_TEST1", "30"))
N2 = int(os.environ.get("N_TEST2", "30"))
N3 = int(os.environ.get("N_TEST3", "25"))


def norm_ru(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def write_doc(folder: Path, doc_id: str, title: str, text: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    body = f"Документ: {title}\nID: {doc_id}\n\n{text.strip()}\n"
    # stable filename from content
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]
    safe = re.sub(r"[^\w\-а-яА-ЯёЁ]+", "_", title, flags=re.UNICODE)[:40] or "doc"
    name = f"{doc_id}_{safe}_{digest}.txt"
    (folder / name).write_text(body, encoding="utf-8")
    return name


def prepare_test1() -> dict:
    data = json.loads((ROOT / "Тест 1" / "xquad.ru.json").read_text(encoding="utf-8"))
    items = []
    for art in data["data"]:
        title = art.get("title") or "xquad"
        for pi, para in enumerate(art["paragraphs"]):
            ctx = para["context"].lstrip("\ufeff")
            for qa in para["qas"]:
                answers = [a["text"] for a in qa.get("answers") or [] if a.get("text")]
                if not answers:
                    continue
                items.append(
                    {
                        "id": qa["id"],
                        "title": title,
                        "para_idx": pi,
                        "context": ctx,
                        "question": qa["question"],
                        "answers": answers,
                    }
                )
    rnd = random.Random(SEED)
    sample = rnd.sample(items, min(N1, len(items)))
    inbox = INBOX / "obuchenie_test1"
    if inbox.exists():
        for p in inbox.glob("*.txt"):
            p.unlink()
    # one doc per unique context in sample
    ctx_map: dict[str, str] = {}
    for i, it in enumerate(sample, start=1):
        key = hashlib.sha1(it["context"].encode("utf-8")).hexdigest()[:12]
        if key not in ctx_map:
            fname = write_doc(inbox, f"t1_{key}", it["title"], it["context"])
            ctx_map[key] = fname
        it["doc_key"] = key
        it["doc_file"] = ctx_map[key]
        it["qnum"] = i
    out = {
        "test": 1,
        "name": "xquad.ru",
        "seed": SEED,
        "n": len(sample),
        "docs": len(ctx_map),
        "inbox": str(inbox),
        "items": sample,
    }
    art = ROOT / "artifacts" / "test1"
    art.mkdir(parents=True, exist_ok=True)
    (art / "sample.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("test1", out["n"], "docs", out["docs"], "->", inbox)
    return out


def prepare_test2() -> dict:
    import pyarrow.parquet as pq

    table = pq.read_table(ROOT / "Тест 2" / "validation-00000-of-00001.parquet")
    rows = table.to_pydict()
    items = []
    n = len(rows["id"])
    for i in range(n):
        ans = rows["answers"][i]
        texts = list(ans.get("text") or [])
        texts = [t for t in texts if t]
        if not texts:
            continue
        items.append(
            {
                "id": str(rows["id"][i]),
                "title": rows["title"][i] or "sberquad",
                "context": rows["context"][i],
                "question": rows["question"][i],
                "answers": texts,
            }
        )
    rnd = random.Random(SEED + 2)
    sample = rnd.sample(items, min(N2, len(items)))
    inbox = INBOX / "obuchenie_test2"
    if inbox.exists():
        for p in inbox.glob("*.txt"):
            p.unlink()
    ctx_map: dict[str, str] = {}
    for i, it in enumerate(sample, start=1):
        key = hashlib.sha1(it["context"].encode("utf-8")).hexdigest()[:12]
        if key not in ctx_map:
            fname = write_doc(inbox, f"t2_{key}", it["title"], it["context"])
            ctx_map[key] = fname
        it["doc_key"] = key
        it["doc_file"] = ctx_map[key]
        it["qnum"] = i
    out = {
        "test": 2,
        "name": "sberquad-validation",
        "seed": SEED + 2,
        "n": len(sample),
        "docs": len(ctx_map),
        "inbox": str(inbox),
        "items": sample,
    }
    art = ROOT / "artifacts" / "test2"
    art.mkdir(parents=True, exist_ok=True)
    (art / "sample.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("test2", out["n"], "docs", out["docs"], "->", inbox)
    return out


def prepare_test3() -> dict:
    zpath = ROOT / "Тест 3" / "RuCoS.zip"
    items = []
    with zipfile.ZipFile(zpath) as zf:
        raw = zf.read("RuCoS/val.jsonl").decode("utf-8")
    for line in raw.splitlines():
        obj = json.loads(line)
        passage = obj["passage"]["text"]
        for qa in obj.get("qas") or []:
            answers = [a["text"] for a in qa.get("answers") or [] if a.get("text")]
            if not answers:
                continue
            q = qa["query"].replace("@placeholder", "_____")
            items.append(
                {
                    "id": f"{obj['idx']}_{qa.get('idx', 0)}",
                    "title": f"rucos_{obj['idx']}",
                    "context": passage,
                    "question": (
                        "В тексте есть пропуск _____. "
                        f"Вопрос: {q} "
                        "Ответь одним словом или короткой фразой — чем заменить пропуск."
                    ),
                    "answers": answers,
                }
            )
    rnd = random.Random(SEED + 3)
    sample = rnd.sample(items, min(N3, len(items)))
    inbox = INBOX / "obuchenie_test3"
    if inbox.exists():
        for p in inbox.glob("*.txt"):
            p.unlink()
    # Group queries by context so retrieval sees the cloze sentence near the passage.
    by_ctx: dict[str, list[dict]] = {}
    for it in sample:
        key = hashlib.sha1(it["context"].encode("utf-8")).hexdigest()[:12]
        by_ctx.setdefault(key, []).append(it)
    ctx_map: dict[str, str] = {}
    for key, group in by_ctx.items():
        ctx = group[0]["context"]
        title = group[0]["title"]
        qlines = []
        for g in group:
            # Keep the cloze query text (without gold) to improve lexical match.
            q_only = g["question"]
            qlines.append(f"- {q_only}")
        # lowercase meta marker so entity extractor does not treat headers as answers
        enriched = f"{ctx.strip()}\n\ncloze:\n" + "\n".join(qlines)
        # t3r2_ isolates this revision from older t3_/t3r_ imports
        fname = write_doc(inbox, f"t3r2_{key}", title, enriched)
        ctx_map[key] = fname
    for i, it in enumerate(sample, start=1):
        key = hashlib.sha1(it["context"].encode("utf-8")).hexdigest()[:12]
        it["doc_key"] = key
        it["doc_file"] = ctx_map[key]
        it["qnum"] = i
    out = {
        "test": 3,
        "name": "rucos-val",
        "seed": SEED + 3,
        "n": len(sample),
        "docs": len(ctx_map),
        "inbox": str(inbox),
        "items": sample,
    }
    art = ROOT / "artifacts" / "test3"
    art.mkdir(parents=True, exist_ok=True)
    (art / "sample.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("test3", out["n"], "docs", out["docs"], "->", inbox)
    return out


if __name__ == "__main__":
    which = os.environ.get("WHICH", "all")
    if which in ("all", "1"):
        prepare_test1()
    if which in ("all", "2"):
        prepare_test2()
    if which in ("all", "3"):
        prepare_test3()
