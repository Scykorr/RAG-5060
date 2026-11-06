#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"

echo "=== rebuild API (Russian answer prompt) ==="
sudo docker compose --profile core up -d --build api | tee artifacts/_ru_rebuild.log | tail -20
bash scripts/wait-healthy.sh

echo "=== fetch corpus ==="
bash scripts/fetch-ru-corpus.sh | tee artifacts/_ru_fetch.log

echo "=== import + chat smoke ==="
bash scripts/smoke-ru-corpus.sh

LAST=$(cat artifacts/.last_ru_corpus)
echo "=== offline chat smoke ==="
bash scripts/smoke-ru-chat-offline.sh "$LAST"
echo "E2E_RU_OK last=$LAST"
