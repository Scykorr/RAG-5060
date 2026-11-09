#!/usr/bin/env bash
set -euo pipefail
BASE="${1:-http://127.0.0.1:8080}"
COOKIE=$(mktemp)
trap 'rm -f "$COOKIE"' EXIT

curl -fsS -c "$COOKIE" -H "Content-Type: application/json" \
  -d '{"login":"admin","password":"admin"}' \
  "$BASE/api/auth/login" | tee /tmp/graph_login.json
echo
curl -fsS -b "$COOKIE" "$BASE/api/graph/status" | tee /tmp/graph_status.json
echo
curl -fsS -b "$COOKIE" "$BASE/api/graph/overview" | tee /tmp/graph_overview.json
echo
DOCS=$(curl -fsS -b "$COOKIE" "$BASE/api/documents?limit=5")
echo "$DOCS" | head -c 1000
echo
DOC_ID=$(python3 - <<'PY' "$DOCS"
import json,sys
d=json.loads(sys.argv[1])
print(d["items"][0]["id"] if d.get("items") else "")
PY
)
echo "DOC_ID=$DOC_ID"
if [[ -n "$DOC_ID" ]]; then
  curl -fsS -b "$COOKIE" -H "Content-Type: application/json" \
    -d "{\"mode\":\"rebuild_doc_graph\",\"document_id\":\"$DOC_ID\"}" \
    "$BASE/api/graph/jobs" | tee /tmp/graph_job.json
  echo
  curl -fsS -b "$COOKIE" "$BASE/api/graph/neighborhood/$DOC_ID?depth=1" | tee /tmp/graph_nb.json
  echo
fi
curl -fsS -b "$COOKIE" -H "Content-Type: application/json" \
  -d '{"mode":"rebuild_collection_graph","dry_run":true}' \
  "$BASE/api/graph/jobs" | tee /tmp/graph_dry.json
echo
UNAUTH=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/graph/status")
echo "unauth_status=$UNAUTH"
test "$UNAUTH" = "401"
echo "SMOKE_OK"
