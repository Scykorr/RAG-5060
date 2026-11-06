#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
ART="${1:-$(cat artifacts/.last_opencorpus 2>/dev/null || echo artifacts/manual-opencorpus-chat)}"
mkdir -p "$ART"
CK=/tmp/rag5060-open.ck
ADMIN_PASSWORD=$(grep ^ADMIN_PASSWORD= .env | cut -d= -f2-)
BASE=http://127.0.0.1:8080
bash scripts/wait-healthy.sh >/dev/null
curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"login\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}" >/dev/null

python3 - <<PY
import json, subprocess
from pathlib import Path
art = Path("$ART")
base="$BASE"
ckfile="/tmp/rag5060-open.ck"

def chat(q, needles, tag):
    payload=json.dumps({"question": q})
    out=subprocess.check_output([
        "curl","-fsS","-m","180","-c",ckfile,"-b",ckfile,
        "-X","POST", f"{base}/api/chat",
        "-H","Content-Type: application/json",
        "-d", payload
    ], text=True)
    (art/f"chat-{tag}.json").write_text(out, encoding="utf-8")
    d=json.loads(out)
    ans=(d.get("answer") or "").lower()
    print("Q:", q)
    print("outcome:", d.get("outcome"), "cites:", len(d.get("citations") or []))
    print("A:", (d.get("answer") or "")[:450])
    assert d.get("outcome") in ("answer","partial"), d
    assert d.get("citations"), d
    assert any(n.lower() in ans for n in needles), (needles, ans[:400])
    print("PASS", tag)
    print()

chat(
    "In Alice in Wonderland, what animal does Alice follow down at the start?",
    ["rabbit"],
    "alice",
)
chat(
    "In Pride and Prejudice, who are Elizabeth and Mr Darcy?",
    ["elizabeth", "darcy", "bennet"],
    "austen",
)
chat(
    "In Frankenstein, who is Victor and what does he create?",
    ["victor", "creature", "frankenstein"],
    "shelley",
)
chat(
    "Where does Sherlock Holmes live with Watson according to the stories?",
    ["baker", "holmes", "watson"],
    "holmes",
)
print("CHAT_OK")
PY
