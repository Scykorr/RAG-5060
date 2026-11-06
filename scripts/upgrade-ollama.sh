#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"

echo "=== pull ollama:0.12.6 ==="
for i in 1 2 3 4 5; do
  echo "ATTEMPT=$i"
  if sudo /usr/bin/docker pull ollama/ollama:0.12.6; then
    echo "PULL_OK"
    break
  fi
  sleep $((10 * i))
done

sudo /usr/bin/docker compose --profile core up -d --force-recreate ollama
sleep 5
sudo /usr/bin/docker compose --profile core ps
echo "=== ollama version ==="
sudo /usr/bin/docker exec rag5060-ollama-1 ollama --version || true
echo "=== pull qwen3:8b ==="
sudo /usr/bin/docker exec rag5060-ollama-1 ollama pull qwen3:8b
echo "=== list models ==="
sudo /usr/bin/docker exec rag5060-ollama-1 ollama list
echo "=== generate smoke ==="
sudo /usr/bin/docker exec rag5060-ollama-1 ollama run qwen3:8b "Reply with exactly: OK" --verbose=false || \
  sudo /usr/bin/docker exec rag5060-ollama-1 curl -s http://127.0.0.1:11434/api/generate \
    -d '{"model":"qwen3:8b","prompt":"Reply with exactly: OK","stream":false,"options":{"num_ctx":4096},"think":false}'
echo
echo "=== nvidia-smi during/after ==="
nvidia-smi 2>/dev/null | head -20 || true
sudo /usr/bin/docker exec rag5060-ollama-1 ollama ps || true
echo DONE
