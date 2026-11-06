#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
ART="artifacts/$(date -u +%Y%m%dT%H%M%SZ)-ru-facts"
mkdir -p "$ART" /mnt/d/RAG5060/inbox/ru_facts
cp -f /mnt/d/RAG5060/inbox/ru_corpus/0281_katalog_faktov_ru.txt /mnt/d/RAG5060/inbox/ru_facts/
cp -f /mnt/d/RAG5060/inbox/ru_corpus/0282_personae_*.txt /mnt/d/RAG5060/inbox/ru_facts/
ls -la /mnt/d/RAG5060/inbox/ru_facts | tee "$ART/facts-ls.txt"

CK=/tmp/rag5060-ru.ck
ADMIN_PASSWORD=$(grep ^ADMIN_PASSWORD= .env | cut -d= -f2-)
BASE=http://127.0.0.1:8080
bash scripts/wait-healthy.sh >/dev/null
curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"login\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}" >/dev/null

curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/documents/import-folder" \
  -H 'Content-Type: application/json' \
  -d '{"subdir":"ru_facts","recursive":true,"skip_existing":true}' | tee "$ART/import.json"

for i in $(seq 1 180); do
  curl -fsS -c "$CK" -b "$CK" "$BASE/api/documents/import-folder/status" > "$ART/import-status.json"
  if python3 -c "import json;d=json.load(open('$ART/import-status.json'));print(d.get('running'),d.get('done'),d.get('skipped'),d.get('failed'),'/',d.get('total'));raise SystemExit(0 if not d.get('running') else 1)"; then
    break
  fi
  sleep 2
done
python3 -c "import json;d=json.load(open('$ART/import-status.json'));assert (d.get('done') or 0)+(d.get('skipped') or 0)>=1,d;print('import_ok',d.get('done'),d.get('skipped'))"

echo "$ART" > artifacts/.last_ru_corpus
bash scripts/smoke-ru-chat-offline.sh "$ART"
echo PASS_FACTS "$ART"
