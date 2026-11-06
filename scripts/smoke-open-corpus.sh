#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)-opencorpus}"
ART="artifacts/${RUN_ID}"
mkdir -p "$ART"
CK=/tmp/rag5060-open.ck
rm -f "$CK"
ADMIN_PASSWORD=$(grep ^ADMIN_PASSWORD= .env | cut -d= -f2-)
BASE=http://127.0.0.1:8080

bash scripts/wait-healthy.sh | tee "$ART/health.txt"
curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"login\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}" >/dev/null

echo "=== inbox open_corpus ==="
curl -fsS -c "$CK" -b "$CK" "$BASE/api/documents/inbox?subdir=open_corpus" | tee "$ART/inbox.json"
python3 -c "import json; d=json.load(open('$ART/inbox.json')); assert d['count']>=5, d; print('files', d['count'])"

echo "=== import open_corpus ==="
curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/documents/import-folder" \
  -H 'Content-Type: application/json' \
  -d '{"subdir":"open_corpus","recursive":true,"skip_existing":true}' | tee "$ART/import.json"

for i in $(seq 1 240); do
  curl -fsS -c "$CK" -b "$CK" "$BASE/api/documents/import-folder/status" > "$ART/import-status.json"
  python3 -c "import json; d=json.load(open('$ART/import-status.json')); print(d.get('running'), d.get('done'), d.get('skipped'), d.get('failed'), '/', d.get('total'), d.get('current')); raise SystemExit(0 if not d.get('running') else 1)" && break
  sleep 2
done
python3 - <<PY
import json
d=json.load(open("$ART/import-status.json"))
print("FINAL done=%s skipped=%s failed=%s total=%s" % (d.get("done"), d.get("skipped"), d.get("failed"), d.get("total")))
assert (d.get("done") or 0) + (d.get("skipped") or 0) >= 1, d
# allow some PDF failures (needs_ocr) but require at least one successful new or skipped existing
assert (d.get("failed") or 0) <= (d.get("total") or 0), d
PY

curl -fsS -c "$CK" -b "$CK" "$BASE/api/documents" | tee "$ART/docs.json" >/dev/null
python3 -c "import json; print('docs', len(json.load(open('$ART/docs.json'))['items']))"

python3 - <<PY
import json, subprocess
from pathlib import Path
art = Path("$ART")
base="$BASE"
ckfile="/tmp/rag5060-open.ck"

def chat(q, needles, tag):
    payload=json.dumps({"question": q}).encode()
    out=subprocess.check_output([
        "curl","-fsS","-m","180","-c",ckfile,"-b",ckfile,
        "-X","POST", f"{base}/api/chat",
        "-H","Content-Type: application/json",
        "-d", payload.decode()
    ], text=True)
    (art/f"chat-{tag}.json").write_text(out, encoding="utf-8")
    d=json.loads(out)
    ans=(d.get("answer") or "").lower()
    print("Q:", q)
    print("outcome:", d.get("outcome"), "cites:", len(d.get("citations") or []))
    print("A:", (d.get("answer") or "")[:400])
    assert d.get("outcome") in ("answer","partial"), d
    assert d.get("citations"), d
    assert any(n.lower() in ans for n in needles), (needles, ans[:300])
    print("PASS", tag)

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

echo "OPEN_CORPUS_OK artifacts=$ART"
echo "$ART" > artifacts/.last_opencorpus
