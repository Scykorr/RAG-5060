#!/usr/bin/env python3
import json
import zipfile
from pathlib import Path
import os

ROOT = Path(os.environ.get("OBUCHENIE_ROOT", Path(__file__).resolve().parents[1]))
z = ROOT / "Тест 3" / "RuCoS.zip"
with zipfile.ZipFile(z) as zf:
    raw = zf.read("RuCoS/val.jsonl").decode("utf-8")
    obj = json.loads(raw.splitlines()[0])
    print("top keys", obj.keys())
    print("passage keys", obj["passage"].keys())
    print("qas count", len(obj.get("qas", [])))
    print(json.dumps(obj.get("qas", [])[:2], ensure_ascii=False, indent=2)[:2000])
    # entity text example
    text = obj["passage"]["text"]
    ents = obj["passage"]["entities"][:5]
    for e in ents:
        print("ent", e, "->", text[e["start"] : e["end"]])
