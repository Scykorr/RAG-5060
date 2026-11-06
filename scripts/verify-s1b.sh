#!/usr/bin/env bash
# S1b acceptance: health, admin, storage, models activate qwen, UI, launcher
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)-s1b}"
ART="artifacts/${RUN_ID}"
mkdir -p "$ART"
CK=/tmp/rag5060-s1b.ck
rm -f "$CK"
ADMIN_PASSWORD=$(grep ^ADMIN_PASSWORD= .env | cut -d= -f2-)
BASE="${BASE_URL:-http://127.0.0.1:8080}"
FAIL=0
pass() { echo "PASS: $*" | tee -a "$ART/results.txt"; }
fail() { echo "FAIL: $*" | tee -a "$ART/results.txt"; FAIL=1; }

echo "=== compose ps ===" | tee "$ART/compose-ps.txt"
sudo docker compose --profile core ps 2>&1 | tee -a "$ART/compose-ps.txt"

echo "=== wait healthy ==="
bash scripts/wait-healthy.sh | tee "$ART/health-wait.txt"
pass "health via $BASE"
cp /tmp/rag-health.json "$ART/health.json" 2>/dev/null || true
echo

echo "=== login ==="
if curl -fsS -m 15 -c "$CK" -b "$CK" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"login\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}" | tee "$ART/login.json"; then
  pass "login"
else
  fail "login"; exit 1
fi
echo

echo "=== admin/system ==="
if curl -fsS -m 20 -c "$CK" -b "$CK" "$BASE/api/admin/system" | tee "$ART/admin-system.json"; then
  python3 -c "import json; d=json.load(open('$ART/admin-system.json')); assert 'services' in d and 'disk' in d and 'queue' in d, d; print('services', list(d['services']))"
  pass "admin/system"
else
  fail "admin/system"
fi
echo

echo "=== admin/storage ==="
if curl -fsS -m 20 -c "$CK" -b "$CK" "$BASE/api/admin/storage" | tee "$ART/admin-storage.json"; then
  pass "admin/storage"
else
  fail "admin/storage"
fi
echo

echo "=== ollama qwen3:8b ==="
if sudo docker exec rag5060-ollama-1 ollama list | tee "$ART/ollama-list.txt" | grep -q 'qwen3:8b'; then
  pass "qwen3:8b in ollama"
else
  fail "qwen3:8b missing"
fi

echo "=== activate qwen3:8b ==="
if curl -fsS -m 300 -c "$CK" -b "$CK" -X POST "$BASE/api/admin/models/activate" \
  -H 'Content-Type: application/json' \
  -d '{"name":"qwen3:8b"}' | tee "$ART/activate.json"; then
  python3 -c "import json; d=json.load(open('$ART/activate.json')); assert d.get('active')=='qwen3:8b', d; print('smoke', (d.get('smoke') or '')[:120])"
  pass "activate qwen3:8b"
else
  fail "activate"
fi
echo

echo "=== models list active ==="
curl -fsS -m 20 -c "$CK" -b "$CK" "$BASE/api/admin/models" | tee "$ART/models-after.json"
python3 -c "import json; d=json.load(open('$ART/models-after.json')); assert d.get('active')=='qwen3:8b', d"
pass "active_model=qwen3:8b"

echo "=== UI static ==="
for p in / /app.js /styles.css; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' -m 10 "$BASE$p" || echo 000)
  if [ "$code" = "200" ]; then pass "GET $p"; else fail "GET $p -> $code"; fi
done

echo "=== launcher binaries ==="
if [ -f dist/RAG5060.exe ]; then pass "dist/RAG5060.exe"; else fail "missing RAG5060.exe"; fi
if [ -f dist/RAG5060-linux-amd64 ]; then pass "dist/RAG5060-linux-amd64"; else fail "missing linux launcher"; fi
chmod +x dist/RAG5060-linux-amd64 2>/dev/null || true
./dist/RAG5060-linux-amd64 status 2>&1 | tee "$ART/launcher-status.txt" || true
pass "launcher status invoked"

echo "=== summary FAIL=$FAIL ===" | tee -a "$ART/results.txt"
echo "$ART" > artifacts/.last_s1b
[ "$FAIL" -eq 0 ] || exit 1
echo "S1B_OK artifacts=$ART"
