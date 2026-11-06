#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"

echo "=== start infra without waiting for ollama ==="
sudo /usr/bin/docker compose --profile core up -d postgres valkey qdrant || true

echo "=== retry ollama pull (up to 3 attempts) ==="
for i in 1 2 3; do
  echo "attempt $i"
  if sudo /usr/bin/docker compose --profile core pull ollama; then
    echo "ollama pull OK"
    break
  fi
  echo "pull failed, sleep 15s"
  sleep 15
done

sudo /usr/bin/docker compose --profile core up -d
sudo /usr/bin/docker compose --profile core ps
df -h /
sudo /usr/bin/docker images --format 'table {{.Repository}}:{{.Tag}}\t{{.Size}}' | head -20
