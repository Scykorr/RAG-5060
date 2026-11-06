#!/usr/bin/env python3
import json
import zipfile
from pathlib import Path

import os

ROOT = Path(os.environ.get("OBUCHENIE_ROOT", Path(__file__).resolve().parents[1]))
z = ROOT / "Тест 3" / "RuCoS.zip"
with zipfile.ZipFile(z) as zf:
    names = zf.namelist()
    print("entries", len(names))
    for n in names:
        print(n)
    for n in names:
        if n.endswith("/") or n.endswith(".txt"):
            continue
        raw = zf.read(n)
        text = raw.decode("utf-8", "replace")
        print("FILE", n, "bytes", len(raw), "lines", len(text.splitlines()))
        print(text.splitlines()[0][:900])
        if len(text.splitlines()) > 1:
            print(text.splitlines()[1][:400])
        break
