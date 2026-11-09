#!/usr/bin/env bash
set -eu
cd "/mnt/d/RAG 5060"
for i in $(seq 1 40); do
  if curl -fsS -m 3 http://127.0.0.1:18080/api/health 2>/dev/null | grep -q '"status":"ok"'; then
    echo healthy_try=$i
    break
  fi
  echo wait=$i
  sleep 3
done
# require 10s stability
for i in 1 2 3 4 5; do
  curl -fsS -m 3 http://127.0.0.1:18080/api/health | grep -q '"status":"ok"'
  sleep 2
done
COOKIE=$(mktemp)
code=$(curl -sS -m 8 -c "$COOKIE" -o /tmp/login.json -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  --data-binary '{"login":"admin","password":"admin"}' \
  http://127.0.0.1:18080/api/auth/login)
echo login=$code $(cat /tmp/login.json)
me=$(curl -sS -m 8 -b "$COOKIE" -o /tmp/me.json -w '%{http_code}' http://127.0.0.1:18080/api/auth/me)
echo me=$me $(cat /tmp/me.json)
test "$code" = "200" && test "$me" = "200" && echo OK
