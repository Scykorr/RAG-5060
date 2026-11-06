#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
CK=/tmp/rag5060-folder.ck
rm -f "$CK"
ADMIN_PASSWORD=$(grep ^ADMIN_PASSWORD= .env | cut -d= -f2-)
BASE=http://127.0.0.1:8080

bash scripts/wait-healthy.sh
curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"login\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}" >/dev/null

echo "=== inbox list ==="
curl -fsS -c "$CK" -b "$CK" "$BASE/api/documents/inbox" | tee /tmp/inbox.json
python3 -c "import json; d=json.load(open('/tmp/inbox.json')); assert d['count']>=2, d; print('files', d['count'], 'hint', d.get('host_hint'))"

echo "=== import folder ==="
curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/documents/import-folder" \
  -H 'Content-Type: application/json' \
  -d '{"subdir":"","recursive":true,"skip_existing":true}' | tee /tmp/imp.json

for i in $(seq 1 60); do
  st=$(curl -fsS -c "$CK" -b "$CK" "$BASE/api/documents/import-folder/status")
  echo "$st" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('running'), d.get('done'), d.get('skipped'), d.get('failed'), d.get('current')); open('/tmp/impst.json','w').write(json.dumps(d))"
  python3 -c "import json; d=json.load(open('/tmp/impst.json')); raise SystemExit(0 if not d.get('running') else 1)" && break
  sleep 1
done

python3 - <<'PY'
import json
d=json.load(open('/tmp/impst.json'))
assert not d.get('running')
assert d.get('done',0) + d.get('skipped',0) >= 1, d
print('IMPORT_OK', d)
PY

curl -fsS -c "$CK" -b "$CK" "$BASE/api/documents" | tee /tmp/docs.json
python3 -c "import json; print('docs', len(json.load(open('/tmp/docs.json'))['items']))"
echo FOLDER_IMPORT_OK
