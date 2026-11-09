#!/usr/bin/env bash
set -euo pipefail
cd "/mnt/d/RAG 5060"
PORT="${RAG_UI_PORT:-18080}"
BASE="http://127.0.0.1:${PORT}"
echo "=== BASE=$BASE ==="
docker compose --profile core up -d
# recreate nginx to pick up port mapping
docker compose --profile core up -d --force-recreate nginx
echo "=== wait health ==="
ok=0
for i in $(seq 1 45); do
  code=$(curl -sS -m 3 -o /tmp/h.json -w '%{http_code}' "$BASE/api/health" || true)
  if [[ "$code" == "200" ]] && python3 -c 'import json; d=json.load(open("/tmp/h.json")); assert d.get("status")=="ok"' 2>/dev/null; then
    echo "health_ok try=$i"
    ok=1
    break
  fi
  echo "health_wait try=$i code=$code body=$(head -c 60 /tmp/h.json 2>/dev/null || true)"
  sleep 2
done
[[ "$ok" == "1" ]] || { echo FAIL_HEALTH; docker compose --profile core ps; docker logs rag5060-api-1 --tail 40; exit 1; }

# require stability: 3 consecutive
for i in 1 2 3; do
  curl -fsS -m 3 "$BASE/api/health" | grep -q '"status":"ok"'
  sleep 1
done
echo "health_stable_ok"

COOKIE=$(mktemp)
code=$(curl -sS -m 5 -o /tmp/login.json -w '%{http_code}' -c "$COOKIE" \
  -H 'Content-Type: application/json' \
  -d '{"login":"admin","password":"admin"}' \
  "$BASE/api/auth/login")
echo "login_http=$code body=$(cat /tmp/login.json)"
[[ "$code" == "200" ]] || { echo FAIL_LOGIN; exit 1; }

code=$(curl -sS -m 5 -o /tmp/me.json -w '%{http_code}' -b "$COOKIE" "$BASE/api/auth/me")
echo "me_http=$code body=$(cat /tmp/me.json)"
[[ "$code" == "200" ]] || { echo FAIL_ME; exit 1; }

# simulate browser: login again after pause
sleep 2
code=$(curl -sS -m 5 -o /tmp/login2.json -w '%{http_code}' -c "$COOKIE" \
  -H 'Content-Type: application/json' \
  -d '{"login":"admin","password":"admin"}' \
  "$BASE/api/auth/login")
[[ "$code" == "200" ]] || { echo FAIL_LOGIN2; exit 1; }

for p in / /app.js /styles.css; do
  c=$(curl -sS -m 3 -o /dev/null -w '%{http_code}' "$BASE$p")
  echo "static $p -> $c"
  [[ "$c" == "200" || "$c" == "304" ]] || exit 1
done
echo LOGIN_E2E_OK port=$PORT
