#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"

# Kill stuck pulls inside container and host docker execs
sudo docker exec rag5060-ollama-1 sh -c 'pkill -f "ollama pull" || true' || true
# Give them a moment
sleep 2

# Also kill any leftover docker exec pull on host (best-effort)
pkill -f 'docker exec rag5060-ollama-1 ollama pull' 2>/dev/null || true
sleep 1

echo "=== before restart ==="
sudo docker exec rag5060-ollama-1 ollama list || true
sudo docker exec rag5060-ollama-1 du -sh /root/.ollama || true

echo "=== starting pull with retries ==="
attempt=1
max=30
while [ "$attempt" -le "$max" ]; do
  echo "--- attempt $attempt/$max $(date -Is) ---"
  if sudo docker exec rag5060-ollama-1 ollama pull qwen3:8b; then
    echo "PULL_OK"
    sudo docker exec rag5060-ollama-1 ollama list
    exit 0
  fi
  ec=$?
  echo "PULL_FAIL exit=$ec"
  sleep $((attempt < 10 ? attempt * 3 : 30))
  attempt=$((attempt + 1))
done
echo "PULL_EXHAUSTED"
exit 1
