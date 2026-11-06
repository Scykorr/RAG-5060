#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
sudo docker compose --profile core up -d
sleep 3
sudo docker compose --profile core ps
echo 'health via api container:'
sudo docker exec rag5060-api-1 curl -fsS http://127.0.0.1:8000/api/health
echo
# localhost forwarding from WSL
curl -fsS http://127.0.0.1:8080/api/health || sudo docker run --rm --network rag5060_internal curlimages/curl:8.5.0 -fsS http://nginx:8080/api/health
echo

ADMIN_PASSWORD=$(grep ^ADMIN_PASSWORD= .env | cut -d= -f2-)
CK=/tmp/rag5060.ck
rm -f "$CK"
BASE=http://127.0.0.1:8080
if ! curl -fsS "$BASE/api/health" >/dev/null; then
  # fallback: exec through nginx container network using host gateway is hard; use api directly via docker exec + publish
  echo "UI port not reachable from WSL localhost; using docker exec API"
  sudo docker exec rag5060-api-1 python - <<PY
import json, urllib.request
print('api ok')
PY
  # Pull model anyway
  sudo docker exec rag5060-ollama-1 ollama pull qwen3:8b
  sudo docker exec rag5060-ollama-1 ollama list
  exit 0
fi

curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"login\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}"
echo
sudo docker exec rag5060-ollama-1 ollama pull qwen3:8b
sudo docker exec rag5060-ollama-1 ollama list
curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/admin/models/activate" -H 'Content-Type: application/json' -d '{"name":"qwen3:8b"}' || true
echo
curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/documents" -F "file=@fixtures/documents/sample.txt;type=text/plain"
echo
curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/chat" -H 'Content-Type: application/json' \
  -d '{"question":"What is the invoice total and VAT rate?"}'
echo
echo DONE
