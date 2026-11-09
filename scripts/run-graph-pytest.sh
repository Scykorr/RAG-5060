#!/usr/bin/env bash
set -euo pipefail
cd "/mnt/d/RAG 5060"
docker run --rm \
  -v "/mnt/d/RAG 5060:/work" \
  -w /work \
  -e ADMIN_PASSWORD=test-admin-password \
  -e SESSION_SECRET=test-session-secret-unit-tests \
  -e DATABASE_URL=postgresql+psycopg://rag:rag@localhost:5432/rag \
  rag5060-api bash -lc 'pip install -q pytest && PYTHONPATH=/work python -m pytest tests/graph tests/unit -q --tb=line'
