#!/usr/bin/env bash
set -eu
cd "/mnt/d/RAG 5060"
mkdir -p artifacts/login-fix
OUT=artifacts/login-fix/run.txt
exec > >(tee "$OUT") 2>&1

echo "=== rebuild api (bootstrap retry) ==="
docker compose --profile core build api

echo "=== up (no force recreate unless needed) ==="
docker compose --profile core up -d

echo "=== wait healthy ==="
ok=0
for i in $(seq 1 60); do
  code=$(curl -sS -m 3 -o /tmp/h.json -w '%{http_code}' http://127.0.0.1:18080/api/health || true)
  if [ "$code" = "200" ] && grep -q '"status":"ok"' /tmp/h.json 2>/dev/null; then
    echo "health_ok try=$i"
    ok=1
    break
  fi
  echo "wait try=$i code=$code"
  sleep 2
done
if [ "$ok" != "1" ]; then
  echo FAIL_HEALTH
  docker compose --profile core ps
  docker logs rag5060-api-1 --tail 60 || true
  exit 1
fi

stable=0
for i in 1 2 3 4 5; do
  curl -fsS -m 3 http://127.0.0.1:18080/api/health | grep -q '"status":"ok"'
  sleep 1
  stable=$((stable + 1))
done
echo "health_stable=$stable"

COOKIE=$(mktemp)
code=$(curl -sS -m 8 -c "$COOKIE" -D /tmp/login.hdr -o /tmp/login.json -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  --data-binary @- http://127.0.0.1:18080/api/auth/login <<'JSON'
{"login":"admin","password":"admin"}
JSON
)
echo "login_admin_http=$code body=$(cat /tmp/login.json)"
grep -i set-cookie /tmp/login.hdr || true

if [ "$code" != "200" ]; then
  echo "trying ADMIN_PASSWORD from container env..."
  PW=$(docker exec rag5060-api-1 printenv ADMIN_PASSWORD)
  printf '{"login":"admin","password":"%s"}' "$PW" > /tmp/login-body.json
  code=$(curl -sS -m 8 -c "$COOKIE" -D /tmp/login2.hdr -o /tmp/login2.json -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    --data-binary @/tmp/login-body.json \
    http://127.0.0.1:18080/api/auth/login)
  echo "login_env_http=$code body=$(cat /tmp/login2.json)"
  grep -i set-cookie /tmp/login2.hdr || true
fi

if [ "$code" != "200" ]; then
  echo FAIL_LOGIN
  docker logs rag5060-api-1 --tail 80
  exit 1
fi

code=$(curl -sS -m 8 -b "$COOKIE" -o /tmp/me.json -w '%{http_code}' http://127.0.0.1:18080/api/auth/me)
echo "me_http=$code body=$(cat /tmp/me.json)"
[ "$code" = "200" ] || { echo FAIL_ME; exit 1; }

code=$(curl -sS -m 8 -b "$COOKIE" -o /tmp/chats.json -w '%{http_code}' http://127.0.0.1:18080/api/chats)
echo "chats_http=$code"
[ "$code" = "200" ] || { echo FAIL_CHATS; exit 1; }

docker compose --profile core ps
echo LOGIN_STABLE_OK
