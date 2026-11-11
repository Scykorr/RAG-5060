#!/usr/bin/env bash
set -euo pipefail
cd "/mnt/d/RAG 5060"
set -a
# shellcheck disable=SC1091
source .env
set +a
# Same volume as compose api service — keeps doc_cache across report runs.
export DATA_ROOT=/var/lib/rag
docker run --rm --network rag5060_internal \
  -e DATABASE_URL -e PYTHONPATH=/work \
  -e DATA_ROOT \
  -e ADMIN_PASSWORD -e SESSION_SECRET \
  -v "/mnt/d/RAG 5060:/work" \
  -v rag5060_ragdata:/var/lib/rag \
  -w /work rag5060-api \
  python scripts/graph_index_report.py --force
