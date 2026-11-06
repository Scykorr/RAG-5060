#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
sudo docker compose --profile core up -d --build api nginx
for i in $(seq 1 60); do
  st=$(sudo docker inspect -f '{{.State.Health.Status}}' rag5060-api-1 2>/dev/null || echo none)
  echo "api=$st"
  if [[ "$st" == "healthy" ]]; then
    break
  fi
  if [[ "$st" == "unhealthy" ]]; then
    sudo docker logs rag5060-api-1 2>&1 | tail -60
    exit 1
  fi
  sleep 2
done
sudo docker compose --profile core ps
curl -fsS http://127.0.0.1:8080/api/health
echo
curl -fsS -c /tmp/rag.ck -b /tmp/rag.ck -X POST http://127.0.0.1:8080/api/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"login\":\"admin\",\"password\":\"$(grep ^ADMIN_PASSWORD= .env | cut -d= -f2-)\"}" || true
echo
curl -fsS -b /tmp/rag.ck http://127.0.0.1:8080/api/admin/system | head -c 500 || true
echo
