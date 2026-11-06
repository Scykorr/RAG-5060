#!/usr/bin/env python3
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import os

ROOT = Path(os.environ.get("OBUCHENIE_ROOT", Path(__file__).resolve().parents[1]))


def inspect_xquad() -> None:
    p = ROOT / "Тест 1" / "xquad.ru.json"
    print("=== TEST1", p, "size", p.stat().st_size)
    d = json.loads(p.read_text(encoding="utf-8"))
    print("top keys", list(d.keys()) if isinstance(d, dict) else type(d))
    data = d.get("data", d) if isinstance(d, dict) else d
    print("articles", len(data))
    art = data[0]
    print("article keys", art.keys())
    para = art["paragraphs"][0]
    print("para keys", para.keys())
    print("context[:200]", para["context"][:200].replace("\n", " "))
    qas = para["qas"][0]
    print("qa", json.dumps(qas, ensure_ascii=False)[:500])
    n_q = sum(len(p["qas"]) for a in data for p in a["paragraphs"])
    print("total_questions", n_q)


def inspect_parquet() -> None:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        import subprocess
        import sys

        subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "pyarrow"])
        import pyarrow.parquet as pq

    base = ROOT / "Тест 2"
    for name in [
        "train-00000-of-00001.parquet",
        "validation-00000-of-00001.parquet",
        "test-00000-of-00001.parquet",
    ]:
        p = base / name
        t = pq.read_table(p)
        print("=== TEST2", name, "rows", t.num_rows, "cols", t.column_names)
        d = t.slice(0, 1).to_pydict()
        for k, v in d.items():
            print(f"  {k}: {str(v[0])[:280].replace(chr(10), ' ')}")


def inspect_rucos() -> None:
    z = ROOT / "Тест 3" / "RuCoS.zip"
    print("=== TEST3", z, "size", z.stat().st_size)
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        print("entries", len(names))
        for n in names[:40]:
            print(" ", n)
        # peek first jsonl/json
        for n in names:
            if n.lower().endswith((".jsonl", ".json")) and not n.endswith("/"):
                raw = zf.read(n)
                print("peek", n, "bytes", len(raw))
                text = raw.decode("utf-8", "replace")
                line = text.splitlines()[0]
                print(line[:700])
                # count lines if jsonl
                if n.endswith(".jsonl") or "\n{" in text[:200]:
                    print("lines", len(text.splitlines()))
                break


if __name__ == "__main__":
    inspect_xquad()
    inspect_parquet()
    inspect_rucos()
