#!/usr/bin/env python3
"""Run RAG chat eval against a prepared sample.json."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(os.environ.get("OBUCHENIE_ROOT", Path(__file__).resolve().parents[1]))
BASE = os.environ.get("RAG_BASE", "http://127.0.0.1:8080")
CK = os.environ.get("RAG_COOKIE", "/tmp/rag-obuchenie.ck")
ATTEMPT = int(os.environ.get("ATTEMPT", "1"))
THRESHOLD = float(os.environ.get("THRESHOLD", "0.65"))


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
        ng = norm_ru(g)
        if not ng:
            continue
        if ng in a or a in ng:
            return True
        gs = _stem_ru(ng)
        if gs and (gs == as_ or gs in a or as_ in ng):
            return True
        # token overlap for multi-word
        gt = set(ng.split())
        at = set(a.split())
        if gt and len(gt & at) / len(gt) >= 0.8:
            return True
    return False


def curl_json(args: list[str], timeout: int = 240) -> dict:
    out = subprocess.check_output(["curl", "-fsS", "-m", str(timeout), *args], text=True)
    return json.loads(out) if out else {}


def login() -> None:
    env = Path("/mnt/d/RAG 5060/.env")
    pw = "admin"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("ADMIN_PASSWORD="):
            pw = line.split("=", 1)[1]
            break
    curl_json(
        [
            "-c",
            CK,
            "-b",
            CK,
            "-X",
            "POST",
            f"{BASE}/api/auth/login",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps({"login": "admin", "password": pw}),
        ]
    )


def import_folder(subdir: str) -> dict:
    start = curl_json(
        [
            "-c",
            CK,
            "-b",
            CK,
            "-X",
            "POST",
            f"{BASE}/api/documents/import-folder",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps({"subdir": subdir, "recursive": True, "skip_existing": True}),
        ]
    )
    for _ in range(900):
        st = curl_json(["-c", CK, "-b", CK, f"{BASE}/api/documents/import-folder/status"])
        print(
            "import",
            st.get("running"),
            st.get("done"),
            st.get("skipped"),
            st.get("failed"),
            "/",
            st.get("total"),
            flush=True,
        )
        if not st.get("running"):
            return st
        time.sleep(2)
    raise TimeoutError("import timeout")


def chat(question: str) -> dict:
    return curl_json(
        [
            "-c",
            CK,
            "-b",
            CK,
            "-X",
            "POST",
            f"{BASE}/api/chat",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps({"question": question}, ensure_ascii=False),
        ],
        timeout=300,
    )


def main() -> None:
    test_n = int(os.environ["TEST_N"])
    sample_path = ROOT / "artifacts" / f"test{test_n}" / "sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    subdir = f"obuchenie_test{test_n}"
    art = ROOT / "artifacts" / f"test{test_n}" / f"attempt{ATTEMPT}"
    art.mkdir(parents=True, exist_ok=True)

    login()
    st = import_folder(subdir)
    (art / "import-status.json").write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")

    results = []
    hits = 0
    for it in sample["items"]:
        q = it["question"]
        try:
            resp = chat(q)
        except Exception as exc:  # noqa: BLE001
            resp = {"error": str(exc), "answer": "", "outcome": "error", "citations": []}
        ans = resp.get("answer") or ""
        ok = contain_hit(ans, it["answers"]) and resp.get("outcome") in ("answer", "partial", None)
        if resp.get("outcome") == "not_found":
            ok = False
        if ok:
            hits += 1
        row = {
            "id": it["id"],
            "question": q,
            "golds": it["answers"],
            "answer": ans,
            "outcome": resp.get("outcome"),
            "citations": len(resp.get("citations") or []),
            "hit": ok,
            "error": resp.get("error"),
        }
        results.append(row)
        print(f"[{'OK' if ok else 'NO'}] {q[:80]} || gold={it['answers'][:2]} || ans={ans[:120]}", flush=True)

    score = hits / max(1, len(results))
    summary = {
        "test": test_n,
        "attempt": ATTEMPT,
        "n": len(results),
        "hits": hits,
        "score": round(score, 4),
        "threshold": THRESHOLD,
        "passed": score >= THRESHOLD,
        "name": sample.get("name"),
    }
    (art / "results.json").write_text(
        json.dumps({"summary": summary, "items": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (art / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY", json.dumps(summary, ensure_ascii=False))
    if not summary["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
