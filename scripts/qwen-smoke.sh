#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

echo "=== list ==="
sudo docker exec rag5060-ollama-1 ollama list

# Prefer host-side HTTP to published port; fallback via docker network
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
if ! curl -fsS -m 5 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  # reach container IP
  CIP=$(sudo docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' rag5060-ollama-1)
  OLLAMA_URL="http://${CIP}:11434"
fi

echo "=== generate via $OLLAMA_URL ==="
curl -sS -m 180 "$OLLAMA_URL/api/generate" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","prompt":"Say OK","stream":false,"options":{"num_predict":16}}'
echo

echo "=== gpu ==="
sudo docker exec rag5060-ollama-1 nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null || echo 'no nvidia-smi'
