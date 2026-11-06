#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
sudo docker compose --profile core stop
sudo docker compose --profile core rm -f postgres api nginx
sudo docker volume rm -f rag5060_pgdata
sudo docker compose --profile core up -d postgres valkey qdrant ollama
for i in $(seq 1 40); do
  if sudo docker exec rag5060-postgres-1 pg_isready -U rag >/dev/null 2>&1; then echo ready; break; fi
  sleep 1
done
sudo docker compose --profile core up -d api nginx
for i in $(seq 1 45); do
  st=$(sudo docker inspect -f '{{.State.Health.Status}}' rag5060-api-1 2>/dev/null || echo none)
  echo api=$st
  if [[ "$st" == "healthy" ]]; then break; fi
  if [[ "$st" == "unhealthy" ]]; then sudo docker logs rag5060-api-1 2>&1 | tail -60; exit 1; fi
  sleep 2
done
sudo docker compose --profile core ps
curl -fsS http://127.0.0.1:8080/api/health; echo
