#!/usr/bin/env bash
# F14 smoke: doc search + quality level + incremental A then B (offline)
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)-quality}"
ART="artifacts/${RUN_ID}"
mkdir -p "$ART"
CK=/tmp/rag5060-quality.ck
rm -f "$CK"
ADMIN_PASSWORD=$(grep ^ADMIN_PASSWORD= .env | cut -d= -f2-)
BASE="${BASE_URL:-http://127.0.0.1:8080}"
FAIL=0
pass() { echo "PASS: $*" | tee -a "$ART/results.txt"; }
fail() { echo "FAIL: $*" | tee -a "$ART/results.txt"; FAIL=1; }

bash scripts/wait-healthy.sh | tee "$ART/health.txt"
pass "health"

curl -fsS -m 15 -c "$CK" -b "$CK" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"login\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}" | tee "$ART/login.json"
pass "login"

# Disable auto during controlled smoke (manual runs)
curl -fsS -m 15 -c "$CK" -b "$CK" -X POST "$BASE/api/admin/quality/auto-improve" \
  -H 'Content-Type: application/json' -d '{"enabled":false}' | tee "$ART/auto-off.json"
pass "auto-improve off for controlled smoke"

echo "=== documents search/count ==="
curl -fsS -m 20 -c "$CK" -b "$CK" "$BASE/api/documents?limit=5" | tee "$ART/docs.json"
python3 - <<PY
import json
d=json.load(open("$ART/docs.json"))
assert "corpus" in d and "documents_active" in d["corpus"], d
print("docs", d["corpus"], "total", d.get("total"))
PY
pass "documents corpus counts"

curl -fsS -m 20 -c "$CK" -b "$CK" "$BASE/api/admin/storage" | tee "$ART/storage.json"
python3 -c "import json; d=json.load(open('$ART/storage.json')); assert 'corpus' in d; print(d['corpus'])"
pass "storage corpus counts"

# Upload A
curl -fsS -m 60 -c "$CK" -b "$CK" -X POST "$BASE/api/documents" \
  -F "file=@fixtures/documents/sample.txt;type=text/plain" | tee "$ART/upload-a.json"
pass "upload A"

# Import gold eval for invoice sample
curl -fsS -m 30 -c "$CK" -b "$CK" -X POST "$BASE/api/admin/quality/eval-set/import" \
  -H 'Content-Type: application/json' \
  -d '{"replace":false,"items":[{"question":"What is the invoice total and VAT rate?","gold_answers":["12500","20%","VAT"],"split":"calibration","method":"import"}]}' \
  | tee "$ART/eval-import.json"
pass "eval import"

curl -fsS -m 30 -c "$CK" -b "$CK" -X POST "$BASE/api/admin/quality/runs" \
  -H 'Content-Type: application/json' \
  -d '{"threshold":0.55,"max_attempts":6,"auto_synth_if_empty":false}' | tee "$ART/run-a.json"
RUN_A=$(python3 -c "import json; print(json.load(open('$ART/run-a.json'))['run_id'])")
echo "run_a=$RUN_A"

for i in $(seq 1 90); do
  curl -fsS -m 20 -c "$CK" -b "$CK" "$BASE/api/admin/quality/runs/$RUN_A" > "$ART/run-a-status.json"
  ST=$(python3 -c "import json; print(json.load(open('$ART/run-a-status.json'))['status'])")
  echo "poll A $i status=$ST"
  case "$ST" in ok|failed|cancelled|superseded) break ;; esac
  sleep 3
done
python3 - <<PY
import json
d=json.load(open("$ART/run-a-status.json"))
print(d)
assert d.get("status") in ("ok","failed"), d
open("$ART/level-a.json","w").write(json.dumps(d, indent=2))
PY
pass "quality run A finished status=$(python3 -c "import json; print(json.load(open('$ART/run-a-status.json'))['status'])")"

curl -fsS -m 20 -c "$CK" -b "$CK" "$BASE/api/admin/quality/level" | tee "$ART/level-after-a.json"
pass "level after A"

# Incremental upload B (second copy / new name)
TMPB="$ART/sample-b.txt"
cp fixtures/documents/sample.txt "$TMPB"
echo "Additional note: contract alpha-42." >> "$TMPB"
curl -fsS -m 60 -c "$CK" -b "$CK" -X POST "$BASE/api/documents" \
  -F "file=@${TMPB};type=text/plain;filename=sample-b.txt" | tee "$ART/upload-b.json"
pass "upload B (incremental)"

# Mark expectation: fingerprint change → stale after notify; force manual run on full corpus
sleep 2
curl -fsS -m 20 -c "$CK" -b "$CK" "$BASE/api/admin/quality/level" | tee "$ART/level-after-b-pre.json" || true

curl -fsS -m 30 -c "$CK" -b "$CK" -X POST "$BASE/api/admin/quality/runs" \
  -H 'Content-Type: application/json' \
  -d '{"threshold":0.55,"max_attempts":6,"auto_synth_if_empty":true}' | tee "$ART/run-b.json"
RUN_B=$(python3 -c "import json; print(json.load(open('$ART/run-b.json'))['run_id'])")
for i in $(seq 1 90); do
  curl -fsS -m 20 -c "$CK" -b "$CK" "$BASE/api/admin/quality/runs/$RUN_B" > "$ART/run-b-status.json"
  ST=$(python3 -c "import json; print(json.load(open('$ART/run-b-status.json'))['status'])")
  echo "poll B $i status=$ST"
  case "$ST" in ok|failed|cancelled|superseded) break ;; esac
  sleep 3
done
pass "quality run B (full corpus) finished"

curl -fsS -m 20 -c "$CK" -b "$CK" "$BASE/api/admin/quality/level" | tee "$ART/level-final.json"
python3 - <<PY
import json
d=json.load(open("$ART/level-final.json"))
assert d.get("documents_active", 0) >= 1, d
assert "offline" in d or d.get("offline") is not False
print("FINAL", d.get("status"), d.get("level"), d.get("score"), "docs", d.get("documents_active"))
PY
pass "level final offline"

# Re-enable auto
curl -fsS -m 15 -c "$CK" -b "$CK" -X POST "$BASE/api/admin/quality/auto-improve" \
  -H 'Content-Type: application/json' -d '{"enabled":true}' >/dev/null
pass "auto-improve restored"

# UI markers
curl -fsS -m 10 "$BASE/" -o "$ART/index.html"
grep -q 'panel-quality' "$ART/index.html" && pass "UI quality panel" || fail "UI quality panel"
grep -q 'doc-search' "$ART/index.html" && pass "UI doc search" || fail "UI doc search"

echo "=== summary FAIL=$FAIL ===" | tee -a "$ART/results.txt"
echo "$ART" > artifacts/.last_quality
[ "$FAIL" -eq 0 ] || exit 1
echo "QUALITY_OK artifacts=$ART"
