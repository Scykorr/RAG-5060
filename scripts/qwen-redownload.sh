#!/usr/bin/env bash
set -eu
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

HASH=a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f
BLOB_NAME="sha256-${HASH}"
URL="https://registry.ollama.ai/v2/library/qwen3/blobs/sha256:${HASH}"
EXPECTED_SIZE=5225374496
LOG="/mnt/d/RAG 5060/logs/qwen-redownload.log"

log() { echo "$*" | tee -a "$LOG"; }

log "START $(date -Is)"

VOL=$(sudo docker volume inspect rag5060_ollamadata -f '{{.Mountpoint}}')
BLOB_DIR="${VOL}/models/blobs"
TMP="${BLOB_DIR}/${BLOB_NAME}.download"
FINAL="${BLOB_DIR}/${BLOB_NAME}"

log "vol=$VOL tmp=$TMP"
sudo mkdir -p "$BLOB_DIR"

sudo docker exec rag5060-ollama-1 sh -c 'pkill -f "/usr/bin/ollama runner" || true; pkill -f "ollama run" || true' || true
sleep 1

# Keep .download for resume; only remove corrupt FINAL
sudo rm -f "$FINAL" || true
sudo rm -rf "${VOL}/models/manifests/registry.ollama.ai/library/qwen3" || true

attempt=1
max=60
while [ "$attempt" -le "$max" ]; do
  log "--- attempt $attempt/$max $(date -Is) ---"
  if sudo test -f "$TMP"; then
    log "resume_from=$(sudo stat -c%s "$TMP")"
  fi

  set +e
  sudo curl -fL --retry 8 --retry-delay 3 --retry-all-errors \
      --connect-timeout 30 --max-time 0 \
      -C - -o "$TMP" "$URL" >>"$LOG" 2>&1
  ec=$?
  set -e
  log "curl_exit=$ec"

  if sudo test -f "$TMP"; then
    SIZE=$(sudo stat -c%s "$TMP")
    log "size=$SIZE expected=$EXPECTED_SIZE"
    if [ "$SIZE" -eq "$EXPECTED_SIZE" ]; then
      log "verifying sha256..."
      GOT=$(sudo sha256sum "$TMP" | awk '{print $1}')
      log "got=$GOT"
      if [ "$GOT" = "$HASH" ]; then
        sudo mv -f "$TMP" "$FINAL"
        sudo chmod 644 "$FINAL"
        log "BLOB_OK"
        break
      fi
      log "HASH_MISMATCH — delete and retry"
      sudo rm -f "$TMP"
    else
      log "SIZE_SHORT — will resume"
    fi
  else
    log "NO_TMP_FILE"
  fi
  attempt=$((attempt + 1))
  sleep $((attempt < 15 ? attempt * 2 : 30))
done

if ! sudo test -f "$FINAL"; then
  log "DOWNLOAD_FAILED"
  exit 1
fi

log "ollama pull register"
sudo docker exec rag5060-ollama-1 ollama pull qwen3:8b >>"$LOG" 2>&1
sudo docker exec rag5060-ollama-1 ollama list | tee -a "$LOG"
log "DONE $(date -Is)"
