#!/usr/bin/env bash
# MVP backup: PostgreSQL dump + originals/extracted snapshot + manifest (F09.2)
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
STAMP=$(date +%Y%m%d-%H%M%S)
DEST="${BACKUP_DIR:-/mnt/d/RAG5060/backups}/${STAMP}"
mkdir -p "$DEST"

echo "backup -> $DEST"
sudo docker exec rag5060-postgres-1 pg_dump -U "${POSTGRES_USER:-rag}" -d "${POSTGRES_DB:-rag}" \
  --no-owner --format=custom -f /tmp/rag.dump
sudo docker cp rag5060-postgres-1:/tmp/rag.dump "$DEST/postgres.dump"
sudo docker exec rag5060-postgres-1 rm -f /tmp/rag.dump

# ragdata volume originals
VOL=$(sudo docker volume inspect rag5060_ragdata -f '{{.Mountpoint}}')
if [ -d "$VOL" ]; then
  sudo tar -C "$VOL" -czf "$DEST/ragdata.tgz" .
fi

# qdrant snapshot best-effort
if sudo docker exec rag5060-qdrant-1 true 2>/dev/null; then
  sudo docker exec rag5060-qdrant-1 sh -c 'ls /qdrant/storage >/dev/null 2>&1' && \
    sudo tar -C "$(sudo docker volume inspect rag5060_qdrantdata -f '{{.Mountpoint}}')" -czf "$DEST/qdrant.tgz" . || \
    echo "qdrant archive skipped" > "$DEST/qdrant.skipped"
fi

python3 - <<PY
import json, os, hashlib, pathlib
dest=pathlib.Path("$DEST")
files=[]
for p in sorted(dest.iterdir()):
    if p.name=="manifest.json": continue
    h=hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    files.append({"name":p.name,"bytes":p.stat().st_size if p.is_file() else 0,"sha256":h})
manifest={"created_at":"$STAMP","files":files,"note":"MVP backup; restore = pg_restore + untar ragdata + re-apply tombstones from PG"}
(dest/"manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps(manifest, indent=2))
PY

echo "BACKUP_OK $DEST"
echo "$DEST"
