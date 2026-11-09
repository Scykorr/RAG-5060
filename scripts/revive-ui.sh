#!/usr/bin/env bash
set -eu
cd "/mnt/d/RAG 5060"
docker compose --profile core up -d
ok=0
for i in $(seq 1 45); do
  if curl -fsS -m 3 http://127.0.0.1:18080/api/health 2>/dev/null | grep -q '"status":"ok"'; then
    echo wsl_health_ok try=$i
    ok=1
    break
  fi
  echo wait=$i
  sleep 2
done
test "$ok" = "1"
for i in 1 2 3 4 5; do
  curl -fsS -m 3 http://127.0.0.1:18080/api/health | grep -q '"status":"ok"'
  sleep 1
done
docker compose --profile core ps
echo REVIVE_OK
