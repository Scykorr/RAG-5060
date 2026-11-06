#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
CIP=$(sudo docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' rag5060-ollama-1)
echo "=== chat ==="
curl -sS -m 180 "http://${CIP}:11434/api/chat" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"Reply with exactly the two letters OK"}],"stream":false,"think":false,"options":{"num_predict":32,"temperature":0}}'
echo
echo "=== list / gpu ==="
sudo docker exec rag5060-ollama-1 ollama list
sudo docker exec rag5060-ollama-1 nvidia-smi --query-gpu=name,memory.used,utilization.gpu --format=csv,noheader
