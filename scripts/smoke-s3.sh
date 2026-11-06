#!/usr/bin/env bash
# S3 reliability: delete/tombstone, retry, restart, ACL 404, backup
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)-s3}"
ART="artifacts/${RUN_ID}"
mkdir -p "$ART"
CK=/tmp/rag5060-s3.ck
rm -f "$CK"
ADMIN_PASSWORD=$(grep ^ADMIN_PASSWORD= .env | cut -d= -f2-)
BASE="${BASE_URL:-http://127.0.0.1:8080}"
FAIL=0
pass() { echo "PASS: $*" | tee -a "$ART/results.txt"; }
fail() { echo "FAIL: $*" | tee -a "$ART/results.txt"; FAIL=1; }

bash scripts/wait-healthy.sh | tee "$ART/health.txt"

curl -fsS -m 15 -c "$CK" -b "$CK" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"login\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}" >/dev/null
pass "login"

echo "=== upload for delete test ==="
curl -fsS -m 60 -c "$CK" -b "$CK" -X POST "$BASE/api/documents" \
  -F "file=@fixtures/documents/sample.txt;type=text/plain" | tee "$ART/upload.json"
DOC=$(python3 -c "import json; print(json.load(open('$ART/upload.json'))['document_id'])")
JOB=$(python3 -c "import json; print(json.load(open('$ART/upload.json'))['job_id'])")

echo "=== chat before delete (must find invoice) ==="
curl -fsS -m 180 -c "$CK" -b "$CK" -X POST "$BASE/api/chat" \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the invoice total?"}' | tee "$ART/chat-before.json"
python3 -c "import json; d=json.load(open('$ART/chat-before.json')); assert d.get('outcome')=='answer' and '12500' in (d.get('answer') or ''), d"
pass "chat before delete"

echo "=== delete / tombstone ==="
curl -fsS -m 15 -c "$CK" -b "$CK" -X DELETE "$BASE/api/documents/$DOC" | tee "$ART/delete.json"
python3 -c "import json; d=json.load(open('$ART/delete.json')); assert d.get('ok') is True, d"
pass "tombstone"

echo "=== list hides deleted ==="
curl -fsS -m 15 -c "$CK" -b "$CK" "$BASE/api/documents" | tee "$ART/docs-after.json"
python3 -c "import json; items=json.load(open('$ART/docs-after.json'))['items']; assert all(i['id']!='$DOC' for i in items), items"
pass "deleted hidden from list"

echo "=== get deleted -> 404 ==="
code=$(curl -sS -o "$ART/get-deleted.json" -w '%{http_code}' -m 10 -c "$CK" -b "$CK" "$BASE/api/documents/$DOC" || true)
[ "$code" = "404" ] && pass "get deleted 404" || fail "get deleted expected 404 got $code"

echo "=== chat after delete must not cite deleted ==="
curl -fsS -m 120 -c "$CK" -b "$CK" -X POST "$BASE/api/chat" \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the invoice total and VAT rate?"}' | tee "$ART/chat-after.json"
python3 - <<PY
import json
d=json.load(open("$ART/chat-after.json"))
cites=d.get("citations") or []
assert all(c.get("document_id")!="$DOC" for c in cites), cites
# either not_found or answer without this doc
print("outcome", d.get("outcome"), "cites", len(cites))
print("AFTER_DELETE_OK")
PY
pass "deleted not retrievable"

echo "=== random UUID ACL/existence 404 ==="
code=$(curl -sS -o /dev/null -w '%{http_code}' -m 10 -c "$CK" -b "$CK" \
  "$BASE/api/documents/00000000-0000-4000-8000-000000000099" || true)
[ "$code" = "404" ] && pass "unknown id 404" || fail "unknown id $code"

echo "=== retry job endpoint ==="
# upload again and retry ready job (idempotent reprocess)
curl -fsS -m 60 -c "$CK" -b "$CK" -X POST "$BASE/api/documents" \
  -F "file=@fixtures/documents/sample.txt;type=text/plain" | tee "$ART/upload2.json"
JOB2=$(python3 -c "import json; print(json.load(open('$ART/upload2.json'))['job_id'])")
curl -fsS -m 60 -c "$CK" -b "$CK" -X POST "$BASE/api/jobs/$JOB2/retry" | tee "$ART/retry.json"
python3 -c "import json; d=json.load(open('$ART/retry.json')); assert d.get('stage')=='ready', d; assert d.get('attempts')>=2, d"
pass "job retry"

echo "=== api restart resilience ==="
sudo docker restart rag5060-api-1
bash scripts/wait-healthy.sh | tee -a "$ART/health-after-restart.txt"
curl -fsS -m 15 -c "$CK" -b "$CK" "$BASE/api/health" | tee "$ART/health-post.json"
curl -fsS -m 15 -c "$CK" -b "$CK" "$BASE/api/documents" | tee "$ART/docs-post-restart.json" >/dev/null
pass "api restart + session still works (or re-login)"
# session cookie may survive; if 401, re-login
if ! curl -fsS -m 15 -c "$CK" -b "$CK" "$BASE/api/documents" >/dev/null 2>&1; then
  curl -fsS -m 15 -c "$CK" -b "$CK" -X POST "$BASE/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"login\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}" >/dev/null
  pass "re-login after restart"
fi

echo "=== backup MVP ==="
OUT=$(bash scripts/backup-mvp.sh | tee "$ART/backup.log" | tail -1)
test -f "$OUT/manifest.json" && test -f "$OUT/postgres.dump" && pass "backup $OUT" || fail "backup missing files"

echo "=== negative retrieval (no stopword leak) ==="
curl -fsS -m 120 -c "$CK" -b "$CK" -X POST "$BASE/api/chat" \
  -H 'Content-Type: application/json' \
  -d '{"question":"quantum teleportation protocol for Martian cheese"}' | tee "$ART/chat-neg.json"
python3 - <<PY
import json
d=json.load(open("$ART/chat-neg.json"))
print(d.get("outcome"), "cites", len(d.get("citations") or []))
assert d.get("outcome") in ("not_found","partial"), d
print("NEG_OK")
PY
pass "negative not_found/partial"

echo "$ART" > artifacts/.last_s3
echo "=== summary FAIL=$FAIL ===" | tee -a "$ART/results.txt"
[ "$FAIL" -eq 0 ] || exit 1
echo "S3_OK artifacts=$ART"
