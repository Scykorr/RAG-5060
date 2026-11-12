#!/usr/bin/env bash
set -euo pipefail
ROOT="/mnt/d/RAG 5060"
VD="$ROOT/apps/web/vendor"
mkdir -p "$VD/d3" "$VD/licenses" "$VD/sigma"

# sigma already fetched; ensure LICENSE
curl -fsSL -o "$VD/licenses/LICENSE-sigma.txt" \
  "https://cdn.jsdelivr.net/npm/sigma@3.0.2/LICENSE" || \
curl -fsSL -o "$VD/licenses/LICENSE-sigma.txt" \
  "https://raw.githubusercontent.com/jacomyal/sigma.js/sigma@3.0.2/LICENSE" || true

# slim d3-force stack
curl -fsSL -o "$VD/d3/d3-dispatch.min.js" "https://cdn.jsdelivr.net/npm/d3-dispatch@3.0.1/dist/d3-dispatch.min.js"
curl -fsSL -o "$VD/d3/d3-timer.min.js" "https://cdn.jsdelivr.net/npm/d3-timer@3.0.1/dist/d3-timer.min.js"
curl -fsSL -o "$VD/d3/d3-quadtree.min.js" "https://cdn.jsdelivr.net/npm/d3-quadtree@3.0.1/dist/d3-quadtree.min.js"
curl -fsSL -o "$VD/d3/d3-force.min.js" "https://cdn.jsdelivr.net/npm/d3-force@3.0.0/dist/d3-force.min.js"

for pkg in d3-force d3-quadtree d3-dispatch d3-timer; do
  curl -fsSL -o "$VD/licenses/LICENSE-${pkg}.txt" "https://cdn.jsdelivr.net/npm/${pkg}@3/LICENSE" || true
done

# Remove bulky full d3 from runtime
if [[ -f "$VD/d3/d3.min.js" ]]; then
  mv -f "$VD/d3/d3.min.js" "$VD/d3/d3.min.js.removed-3b"
fi
rm -f "$VD/sigma/sigma-gh.min.js"

# Try optional plugins via GitHub/npm (best-effort)
mkdir -p "$VD/sigma/plugins"
curl -fsSL -o "$VD/sigma/plugins/node-border.esm.js" \
  "https://cdn.jsdelivr.net/npm/@sigma/node-border@3.0.0/dist/sigma-node-border.esm.js" || \
curl -fsSL -o "$VD/sigma/plugins/node-border.esm.js" \
  "https://cdn.jsdelivr.net/npm/@sigma/node-border@3.0.0/+esm" || \
  echo "WARN node-border missing"

curl -fsSL -o "$VD/sigma/plugins/edge-curve.esm.js" \
  "https://cdn.jsdelivr.net/npm/@sigma/edge-curve@3.0.0/dist/sigma-edge-curve.esm.js" || \
curl -fsSL -o "$VD/sigma/plugins/edge-curve.esm.js" \
  "https://cdn.jsdelivr.net/npm/@sigma/edge-curve@3.0.0/+esm" || \
  echo "WARN edge-curve missing"

# SHA256 inventory
(
  cd "$VD"
  : > SHA256SUMS.txt
  find . -type f \( -name '*.js' -o -name '*.removed-3b' \) | sort | while read -r f; do
    sha256sum "$f" >> SHA256SUMS.txt
  done
)

echo "=== sizes ==="
du -h "$VD/sigma/sigma.min.js" "$VD/d3"/* "$VD/graphology/graphology.umd.min.js" 2>/dev/null || true
echo "=== sha ==="
cat "$VD/SHA256SUMS.txt"
