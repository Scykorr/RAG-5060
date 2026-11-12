#!/usr/bin/env bash
set -euo pipefail
cd "/mnt/d/RAG 5060"
set -a
# shellcheck disable=SC1091
source .env
set +a
docker run --rm --network rag5060_internal \
  -e DATABASE_URL -e PYTHONPATH=/work -e DATA_ROOT=/var/lib/rag \
  -e ADMIN_PASSWORD -e SESSION_SECRET -e OLLAMA_HOST \
  -v "/mnt/d/RAG 5060:/work" -v rag5060_ragdata:/var/lib/rag \
  -w /work rag5060-api \
  python -u scripts/graph_topics_report.py "$@" | tee artifacts/_topics2c_report.log
