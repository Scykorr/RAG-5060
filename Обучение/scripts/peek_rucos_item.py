#!/usr/bin/env python3
import json
import zipfile
from pathlib import Path
import os

ROOT = Path(os.environ.get("OBUCHENIE_ROOT", Path(__file__).resolve().parents[1]))
z = ROOT / "Тест 3" / "RuCoS.zip"
with zipfile.ZipFile(z) as zf:
    # use val (smaller) for peek
    raw = zf.read("RuCoS/val.jsonl").decode("utf-8")
    lines = raw.splitlines()
    print("val_lines", len(lines))
    obj = json.loads(lines[0])
    print(json.dumps(obj, ensure_ascii=False, indent=2)[:2500])
