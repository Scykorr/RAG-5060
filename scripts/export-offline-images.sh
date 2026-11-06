#!/usr/bin/env bash
# Optional offline image pack for portable transfer (F12.3).
# Run on a machine that already pulled/built the stack images.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/dist/offline-images}"
mkdir -p "$OUT"

IMAGES=(
  "postgres:16.6-alpine"
  "valkey/valkey:8.0.2-alpine"
  "qdrant/qdrant:v1.13.2"
  "ollama/ollama:0.12.6"
  "nginx:1.27-alpine"
)

echo "[offline] saving base images → $OUT"
for img in "${IMAGES[@]}"; do
  safe="$(echo "$img" | tr '/:' '__')"
  echo "  docker save $img"
  docker save -o "$OUT/${safe}.tar" "$img"
done

# Local API image if present
if docker image inspect rag5060-api >/dev/null 2>&1; then
  docker save -o "$OUT/rag5060-api.tar" rag5060-api
elif docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -q 'rag5060.*api'; then
  API_IMG="$(docker image ls --format '{{.Repository}}:{{.Tag}}' | grep 'rag5060' | grep 'api' | head -n1)"
  docker save -o "$OUT/rag5060-api.tar" "$API_IMG"
else
  echo "[offline] note: build api once (compose up --build) then re-run to include rag5060-api.tar"
fi

cat >"$OUT/load-images.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
for f in "$DIR"/*.tar; do
  echo "loading $f"
  docker load -i "$f"
done
echo "done — place .env + launcher + compose, then: RAG5060 start"
EOF
chmod +x "$OUT/load-images.sh"

echo "[offline] pack ready: $OUT"
echo "Copy with: launcher binary, docker-compose.yml, configs/, .env.example, apps/web/, this folder."
