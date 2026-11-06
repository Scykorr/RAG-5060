#!/usr/bin/env bash
# Download Python wheels ONCE (online) for later offline Docker builds / harness.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_WHEELS="$ROOT/vendor/python-wheels"
TOOLS_WHEELS="$ROOT/vendor/python-wheels-tools"
mkdir -p "$API_WHEELS" "$TOOLS_WHEELS"

# Prefer docker python (host may lack pip). Needs network once.
docker run --rm \
  -v "$ROOT/requirements.txt:/req/requirements.txt:ro" \
  -v "$API_WHEELS:/wheels" \
  python:3.12-slim-bookworm \
  bash -lc 'pip install -q --upgrade pip && pip download -d /wheels -r /req/requirements.txt'

docker run --rm \
  -v "$ROOT/Обучение/requirements-tools.txt:/req/requirements-tools.txt:ro" \
  -v "$TOOLS_WHEELS:/wheels" \
  python:3.12-slim-bookworm \
  bash -lc 'pip install -q --upgrade pip && pip download -d /wheels -r /req/requirements-tools.txt'

echo "OK: $API_WHEELS ($(ls -1 "$API_WHEELS" | grep -c '\.whl$' || true) wheels)"
echo "OK: $TOOLS_WHEELS ($(ls -1 "$TOOLS_WHEELS" | grep -c '\.whl$' || true) wheels)"
