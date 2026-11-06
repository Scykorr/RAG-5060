#!/usr/bin/env bash
set -eu
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
LOG="/mnt/d/RAG 5060/logs/qwen-aria2.log"
HASH=a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f
BLOB_NAME="sha256-${HASH}"
URL="https://registry.ollama.ai/v2/library/qwen3/blobs/sha256:${HASH}"
EXPECTED_SIZE=5225374496
# Download on D: (fast, resumable), then install into docker volume
DL_DIR="/mnt/d/RAG5060/models"
TMP="${DL_DIR}/${BLOB_NAME}.download"

log() { echo "$*" | tee -a "$LOG"; }
log "START $(date -Is)"

pkill -f 'qwen-redownload.sh' 2>/dev/null || true
pkill -f "curl.*${HASH}" 2>/dev/null || true
sleep 1

command -v aria2c >/dev/null 2>&1 || {
  log "installing aria2"
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq aria2
}

mkdir -p "$DL_DIR"

VOL=$(sudo docker volume inspect rag5060_ollamadata -f '{{.Mountpoint}}')
BLOB_DIR="${VOL}/models/blobs"
FINAL="${BLOB_DIR}/${BLOB_NAME}"
sudo mkdir -p "$BLOB_DIR"
sudo rm -f "$FINAL" || true
sudo rm -rf "${VOL}/models/manifests/registry.ollama.ai/library/qwen3" || true

# Reuse any partial already in docker volume if larger
VOL_TMP="${BLOB_DIR}/${BLOB_NAME}.download"
if sudo test -f "$VOL_TMP"; then
  VS=$(sudo stat -c%s "$VOL_TMP")
  TS=0
  [ -f "$TMP" ] && TS=$(stat -c%s "$TMP")
  if [ "$VS" -gt "$TS" ]; then
    log "importing volume partial ($VS bytes) to D:"
    sudo cp -f "$VOL_TMP" "$TMP"
    sudo chown "$(id -u):$(id -g)" "$TMP"
  fi
fi

attempt=1
while [ "$attempt" -le 40 ]; do
  log "--- aria2 attempt $attempt $(date -Is) ---"
  [ -f "$TMP" ] && log "resume_from=$(stat -c%s "$TMP")"
  set +e
  aria2c -c -x 8 -s 8 -k 1M --file-allocation=none \
    --max-tries=0 --retry-wait=3 --timeout=60 --connect-timeout=30 \
    --allow-overwrite=true --auto-file-renaming=false \
    -d "$DL_DIR" -o "${BLOB_NAME}.download" "$URL" >>"$LOG" 2>&1
  ec=$?
  set -e
  log "aria2_exit=$ec"
  if [ -f "$TMP" ]; then
    SIZE=$(stat -c%s "$TMP")
    log "size=$SIZE expected=$EXPECTED_SIZE"
    if [ "$SIZE" -eq "$EXPECTED_SIZE" ]; then
      GOT=$(sha256sum "$TMP" | awk '{print $1}')
      log "got=$GOT"
      if [ "$GOT" = "$HASH" ]; then
        log "installing into ollama volume"
        sudo cp -f "$TMP" "$FINAL"
        sudo chmod 644 "$FINAL"
        log "BLOB_OK"
        break
      fi
      log "HASH_MISMATCH wipe"
      rm -f "$TMP" "${TMP}.aria2" || true
    fi
  else
    log "NO_TMP"
  fi
  attempt=$((attempt + 1))
  sleep 5
done

if ! sudo test -f "$FINAL"; then
  log "DOWNLOAD_FAILED"
  exit 1
fi

log "ollama pull register"
sudo docker exec rag5060-ollama-1 ollama pull qwen3:8b >>"$LOG" 2>&1
sudo docker exec rag5060-ollama-1 ollama list | tee -a "$LOG"
log "DONE $(date -Is)"
