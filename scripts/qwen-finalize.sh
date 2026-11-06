#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

BLOB_DIR=/root/.ollama/models/blobs
HASH=sha256-a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f
EXPECTED=5225374496

echo "=== finalize main blob if complete ==="
SIZE=$(sudo docker exec rag5060-ollama-1 stat -c%s "$BLOB_DIR/${HASH}-partial" 2>/dev/null || echo 0)
echo "partial_size=$SIZE expected=$EXPECTED"

if [ "$SIZE" = "$EXPECTED" ]; then
  sudo docker exec rag5060-ollama-1 sh -c "
    set -e
    cd $BLOB_DIR
    if [ ! -f $HASH ]; then
      mv ${HASH}-partial $HASH
      rm -f ${HASH}-partial-*
      echo RENAMED_OK
    else
      echo ALREADY_FINAL
      rm -f ${HASH}-partial ${HASH}-partial-*
    fi
    ls -lh $HASH
  "
else
  echo "BLOB_INCOMPLETE size=$SIZE"
fi

echo "=== fetch manifest layers ==="
# Get manifest to see remaining digests
curl -fsSL -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
  'https://registry.ollama.ai/v2/library/qwen3/manifests/8b' | tee /tmp/qwen3-8b.manifest.json | head -c 2000
echo
echo "=== pull again (should skip finished blob) ==="
sudo docker exec rag5060-ollama-1 ollama pull qwen3:8b
echo "=== list ==="
sudo docker exec rag5060-ollama-1 ollama list
