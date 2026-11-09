#!/usr/bin/env bash
set -euo pipefail
cd "/mnt/d/RAG 5060"
docker compose --profile core up -d
for i in $(seq 1 20); do
  if curl -fsS -m 3 http://127.0.0.1:18080/api/health 2>/dev/null | grep -q '"status":"ok"'; then
    echo UP
    curl -fsS http://127.0.0.1:18080/api/health
    echo
    docker compose --profile core ps --format '{{.Name}} {{.Status}} {{.Ports}}'
    exit 0
  fi
  echo "wait$i"
  sleep 3
done
docker compose --profile core ps
docker logs rag5060-nginx-1 --tail 30 || true
docker logs rag5060-api-1 --tail 30 || true
exit 1
