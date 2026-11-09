#!/usr/bin/env bash
set -euo pipefail
BASE=http://127.0.0.1:8080
COOKIE=$(mktemp)
trap 'rm -f "$COOKIE"' EXIT
curl -fsS -c "$COOKIE" -H "Content-Type: application/json" \
  -d '{"login":"admin","password":"admin"}' "$BASE/api/auth/login" >/dev/null
DOC=e2f811ee-ed7e-485e-93d6-b4c1a91d0f9c
EDGE=e37ad2d3-b640-41ba-80d8-507119a11dce
echo "=== evidence ==="
curl -fsS -b "$COOKIE" "$BASE/api/graph/edges/$EDGE/evidence"
echo
echo "=== rebuild twice ==="
curl -fsS -b "$COOKIE" -H "Content-Type: application/json" \
  -d "{\"mode\":\"rebuild_doc_graph\",\"document_id\":\"$DOC\"}" "$BASE/api/graph/jobs"
echo
curl -fsS -b "$COOKIE" -H "Content-Type: application/json" \
  -d "{\"mode\":\"rebuild_doc_graph\",\"document_id\":\"$DOC\"}" "$BASE/api/graph/jobs"
echo
echo "=== status ==="
curl -fsS -b "$COOKIE" "$BASE/api/graph/status"
echo
echo "=== relations ==="
curl -fsS -b "$COOKIE" "$BASE/api/graph/documents/$DOC/relations"
echo
echo "=== chat create ==="
curl -fsS -b "$COOKIE" -H "Content-Type: application/json" \
  -d '{"title":"graph-smoke"}' "$BASE/api/chats"
echo
echo EVIDENCE_SMOKE_OK
