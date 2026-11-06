#!/usr/bin/env python3
import json
from pathlib import Path

art = Path("/mnt/d/RAG 5060/Обучение/artifacts/test3/attempt3/results.json")
d = json.loads(art.read_text(encoding="utf-8"))
print("summary", d["summary"])
for it in d["items"]:
    if it["hit"]:
        continue
    print("---")
    print("Q:", it["question"][:180])
    print("gold:", it["golds"][:5])
    print("ans:", (it["answer"] or "")[:200])
    print("outcome:", it["outcome"], "cites:", it["citations"])
