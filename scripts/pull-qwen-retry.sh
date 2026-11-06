#!/usr/bin/env bash
set -uo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
MODEL="${1:-qwen3:8b}"
for i in 1 2 3 4 5 6 7 8; do
  echo "===== ATTEMPT $i pull $MODEL ====="
  if sudo docker exec rag5060-ollama-1 ollama pull "$MODEL"; then
    echo "PULL_OK"
    sudo docker exec rag5060-ollama-1 ollama list
    exit 0
  fi
  echo "PULL_FAIL attempt=$i; sleep $((15*i))"
  sleep $((15 * i))
done
echo "ALL_FAILED"
sudo docker exec rag5060-ollama-1 ollama list || true
exit 1
