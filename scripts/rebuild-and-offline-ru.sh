#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
sudo docker compose --profile core up -d --build api
bash scripts/wait-healthy.sh
LAST=$(cat artifacts/.last_ru_corpus)
bash scripts/smoke-ru-chat-offline.sh "$LAST"
echo OK
