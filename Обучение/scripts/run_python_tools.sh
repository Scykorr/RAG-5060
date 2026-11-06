#!/usr/bin/env bash
# Run Обучение Python tools offline via vendored wheels (no pip from network).
# Usage: bash Обучение/scripts/run_python_tools.sh 'python /data/scripts/prepare_samples.py'
set -euo pipefail
ROOT="/mnt/d/RAG 5060"
OB="$ROOT/Обучение"
WHEELS="$ROOT/vendor/python-wheels-tools"
CMD="${1:?pass python command as first arg}"
if ! ls "$WHEELS"/*.whl >/dev/null 2>&1; then
  echo "ERROR: $WHEELS empty. Once online run: bash scripts/vendor-python-wheels.sh" >&2
  exit 2
fi
exec sudo docker run --rm \
  -e OBUCHENIE_ROOT=/data \
  -e RAG_INBOX=/inbox \
  -e SAMPLE_SEED="${SAMPLE_SEED:-5060}" \
  -e N_TEST1="${N_TEST1:-20}" \
  -e N_TEST2="${N_TEST2:-20}" \
  -e N_TEST3="${N_TEST3:-20}" \
  -e WHICH="${WHICH:-}" \
  -v "$OB:/data" \
  -v "/mnt/d/RAG5060/inbox:/inbox" \
  -v "$WHEELS:/wheels:ro" \
  python:3.12-slim-bookworm \
  bash -lc "pip install --no-index --find-links=/wheels -q -r /data/requirements-tools.txt && $CMD"
