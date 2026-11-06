#!/usr/bin/env bash
set -uo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"

TAG="${1:-ollama/ollama:0.6.5}"
echo "target=$TAG"

for i in 1 2 3 4 5 6 7 8; do
  echo "ATTEMPT=$i"
  if sudo /usr/bin/docker pull "$TAG"; then
    echo "SUCCESS"
    sudo /usr/bin/docker compose --profile core up -d ollama
    sudo /usr/bin/docker compose --profile core ps
    exit 0
  fi
  echo "FAIL attempt $i"
  sleep $((10 * i))
done

echo "ALL_ATTEMPTS_FAILED"
sudo /usr/bin/docker compose --profile core ps
exit 1
