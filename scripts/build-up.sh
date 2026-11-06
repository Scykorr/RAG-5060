#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
sudo /usr/bin/docker compose --profile core up -d --build
sudo /usr/bin/docker compose --profile core ps
echo '--- health ---'
sleep 5
curl -fsS http://127.0.0.1:8080/api/health || true
echo
curl -fsS http://127.0.0.1:8080/ || head -c 200
echo
