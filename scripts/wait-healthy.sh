#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
deadline=$((SECONDS + 180))
while (( SECONDS < deadline )); do
  if curl -fsS -m 5 http://127.0.0.1:8080/api/health >/tmp/rag-health.json 2>/dev/null; then
    cat /tmp/rag-health.json
    echo
    echo HEALTHY
    exit 0
  fi
  # fallback via api container
  if sudo docker exec rag5060-api-1 curl -fsS -m 3 http://127.0.0.1:8000/api/health >/tmp/rag-health.json 2>/dev/null; then
    echo "api-container healthy; waiting for nginx..."
  fi
  sleep 3
done
echo UNHEALTHY
sudo docker compose --profile core -f "/mnt/d/RAG 5060/docker-compose.yml" ps || true
exit 1
