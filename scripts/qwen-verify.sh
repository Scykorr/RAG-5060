#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
BLOB=/root/.ollama/models/blobs/sha256-a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f
EXPECTED=a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f
echo "=== sha256 (may take ~1-2 min) ==="
GOT=$(sudo docker exec rag5060-ollama-1 sha256sum "$BLOB" | awk '{print $1}')
echo "expected=$EXPECTED"
echo "got     =$GOT"
if [ "$GOT" = "$EXPECTED" ]; then echo HASH_OK; else echo HASH_BAD; exit 1; fi

echo "=== cpu-only chat ==="
CIP=$(sudo docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' rag5060-ollama-1)
curl -sS -m 300 "http://${CIP}:11434/api/chat" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"Say OK"}],"stream":false,"options":{"num_predict":32,"temperature":0.7,"num_gpu":0}}'
echo
