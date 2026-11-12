#!/usr/bin/env bash
# Slice 3b step 0: sigma 3.x + slim d3-force stack (offline SPA vendor).
set -euo pipefail
ROOT="/mnt/d/RAG 5060"
VD="$ROOT/apps/web/vendor"
mkdir -p "$VD/graphology" "$VD/sigma" "$VD/d3" "$VD/licenses" "$VD/sigma/plugins"

fetch() {
  local url="$1" out="$2"
  echo "GET $url"
  curl -fsSL --retry 3 --retry-delay 1 -o "$out" "$url"
  local bytes
  bytes=$(wc -c < "$out" | tr -d ' ')
  local sha
  sha=$(sha256sum "$out" | awk '{print $1}')
  echo "  -> ${bytes} bytes sha256=${sha}"
  printf '%s  %s\n' "$sha" "$(basename "$out")" >> "$VD/SHA256SUMS.txt"
}

: > "$VD/SHA256SUMS.txt"

# graphology (keep 0.25.4 UMD — known good with our adapter; 0.26 also OK)
fetch "https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js" \
  "$VD/graphology/graphology.umd.min.js"
fetch "https://cdn.jsdelivr.net/npm/graphology@0.25.4/LICENSE.txt" \
  "$VD/licenses/LICENSE-graphology.txt" || true

# sigma 3.0.2 (MIT) — user-specified 3.x line (not 2.4, not 4.x)
fetch "https://cdn.jsdelivr.net/npm/sigma@3.0.2/build/sigma.min.js" \
  "$VD/sigma/sigma.min.js"
fetch "https://cdn.jsdelivr.net/npm/sigma@3.0.2/LICENSE.txt" \
  "$VD/licenses/LICENSE-sigma.txt" || \
fetch "https://cdn.jsdelivr.net/npm/sigma@3.0.2/LICENSE" \
  "$VD/licenses/LICENSE-sigma.txt" || true

# Optional plugins (ESM). Store for later import maps; not required for step 0 load.
fetch "https://cdn.jsdelivr.net/npm/@sigma/node-border@3.0.0/+esm" \
  "$VD/sigma/plugins/node-border.esm.js" || echo "WARN: node-border ESM skip"
fetch "https://cdn.jsdelivr.net/npm/@sigma/edge-curve@3.0.0/+esm" \
  "$VD/sigma/plugins/edge-curve.esm.js" || echo "WARN: edge-curve ESM skip"
# Licenses for plugins if present on npm
fetch "https://cdn.jsdelivr.net/npm/@sigma/node-border@3.0.0/LICENSE" \
  "$VD/licenses/LICENSE-sigma-node-border.txt" || true
fetch "https://cdn.jsdelivr.net/npm/@sigma/edge-curve@3.0.0/LICENSE" \
  "$VD/licenses/LICENSE-sigma-edge-curve.txt" || true

# Slim d3-force stack only (no full d3.min.js)
fetch "https://cdn.jsdelivr.net/npm/d3-dispatch@3.0.1/dist/d3-dispatch.min.js" \
  "$VD/d3/d3-dispatch.min.js"
fetch "https://cdn.jsdelivr.net/npm/d3-timer@3.0.1/dist/d3-timer.min.js" \
  "$VD/d3/d3-timer.min.js"
fetch "https://cdn.jsdelivr.net/npm/d3-quadtree@3.0.1/dist/d3-quadtree.min.js" \
  "$VD/d3/d3-quadtree.min.js"
fetch "https://cdn.jsdelivr.net/npm/d3-force@3.0.0/dist/d3-force.min.js" \
  "$VD/d3/d3-force.min.js"

for pkg in d3-force d3-quadtree d3-dispatch d3-timer; do
  fetch "https://cdn.jsdelivr.net/npm/${pkg}@3/LICENSE" \
    "$VD/licenses/LICENSE-${pkg}.txt" || \
  fetch "https://cdn.jsdelivr.net/npm/${pkg}@3/LICENSE.txt" \
    "$VD/licenses/LICENSE-${pkg}.txt" || true
done

# Drop bulky full d3 UMD from runtime path (keep a .bak note in README sizes)
if [[ -f "$VD/d3/d3.min.js" ]]; then
  mv -f "$VD/d3/d3.min.js" "$VD/d3/d3.min.js.removed-3b"
fi
if [[ -f "$VD/licenses/LICENSE-d3.txt" ]]; then
  # keep license for historical; force stack still ISC
  true
fi

echo "=== vendored ==="
find "$VD" -type f \( -name '*.js' -o -name 'LICENSE*' -o -name 'SHA256SUMS.txt' \) | sort
du -sh "$VD/graphology" "$VD/sigma" "$VD/d3" "$VD/licenses" || true
cat "$VD/SHA256SUMS.txt"
