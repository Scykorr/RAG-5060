#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
ART="artifacts/smoke-offline-ui-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$ART"

echo "[1] rebuild api"
sudo docker compose --profile core up -d --build api nginx | tee "$ART/rebuild.log"

echo "[2] wait healthy"
for i in $(seq 1 90); do
  st=$(sudo docker inspect -f '{{.State.Health.Status}}' rag5060-api-1 2>/dev/null || echo none)
  echo "api=$st"
  if [[ "$st" == "healthy" ]]; then
    break
  fi
  if [[ "$st" == "unhealthy" ]]; then
    sudo docker logs rag5060-api-1 2>&1 | tee "$ART/api-unhealthy.log" | tail -80
    exit 1
  fi
  sleep 2
done

curl -fsS http://127.0.0.1:8080/api/health | tee "$ART/health.json"
echo

echo "[3] login admin/admin"
curl -sS -c "$ART/cookie.jar" -b "$ART/cookie.jar" -X POST http://127.0.0.1:8080/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"login":"admin","password":"admin"}' | tee "$ART/login.json"
echo
grep -q '"login":"admin"' "$ART/login.json"

curl -fsS -b "$ART/cookie.jar" http://127.0.0.1:8080/api/auth/me | tee "$ART/me.json"
echo

echo "[4] chats + dispatcher"
curl -fsS -b "$ART/cookie.jar" http://127.0.0.1:8080/api/chats | tee "$ART/chats.json"
echo
curl -fsS -b "$ART/cookie.jar" http://127.0.0.1:8080/api/admin/dispatcher | tee "$ART/dispatcher.json" >/dev/null
python3 - <<'PY' "$ART/dispatcher.json"
import json,sys
p=sys.argv[1]
d=json.load(open(p))
assert "generated_at" in d and "corpus" in d and "queues" in d and "services" in d
print("dispatcher_ok", d.get("active_model"), "docs", d["corpus"].get("documents_active"), "services_ok", d.get("services_ok"))
PY

echo "[5] create chat session + ask"
curl -fsS -b "$ART/cookie.jar" -X POST http://127.0.0.1:8080/api/chats \
  -H 'Content-Type: application/json' \
  -d '{"title":"Smoke offline"}' | tee "$ART/new_chat.json"
echo
SID=$(python3 -c 'import json;print(json.load(open("'"$ART"'/new_chat.json"))["id"])')
curl -fsS -b "$ART/cookie.jar" -X POST http://127.0.0.1:8080/api/chat \
  -H 'Content-Type: application/json' \
  -d "{\"question\":\"Who is Alice?\",\"session_id\":\"$SID\"}" | tee "$ART/chat_answer.json"
echo
curl -fsS -b "$ART/cookie.jar" "http://127.0.0.1:8080/api/chats/$SID" | tee "$ART/chat_get.json"
echo
python3 - <<'PY' "$ART/chat_get.json"
import json,sys
d=json.load(open(sys.argv[1]))
msgs=d.get("messages") or []
assert len(msgs)>=2, msgs
print("history_ok", len(msgs), "title", d.get("title"))
PY

echo "[6] offline static UI"
for p in / /app.js /styles.css; do
  code=$(curl -fsS -o /dev/null -w '%{http_code}' "http://127.0.0.1:8080$p")
  echo "$p -> $code"
  [[ "$code" == "200" ]]
done
if grep -ERi 'cdn\.|fonts\.googleapis|unpkg\.com|jsdelivr|cdnjs' apps/web/; then
  echo "FAIL: remote asset refs found" | tee "$ART/fail.txt"
  exit 1
fi
echo offline-assets-ok | tee "$ART/offline.txt"
echo PASS | tee "$ART/PASS.txt"
echo "artifacts=$ART"
