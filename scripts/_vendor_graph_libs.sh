#!/usr/bin/env bash
# One-shot online vendoring of graphology / sigma / d3-force for offline SPA.
set -euo pipefail
ROOT="/mnt/d/RAG 5060"
VD="$ROOT/apps/web/vendor"
mkdir -p "$VD/graphology" "$VD/sigma" "$VD/d3" "$VD/licenses"

fetch() {
  local url="$1" out="$2"
  echo "GET $url"
  curl -fsSL --retry 3 --retry-delay 1 -o "$out" "$url"
  echo "  -> $(wc -c < "$out") bytes"
}

# --- graphology (UMD, attaches window.graphology) ---
fetch "https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js" \
  "$VD/graphology/graphology.umd.min.js"
fetch "https://cdn.jsdelivr.net/npm/graphology@0.25.4/LICENSE.txt" \
  "$VD/licenses/LICENSE-graphology.txt" || true

# --- sigma 2.4 (UMD WebGL + graphology) — no published sigma v4; 2.4 is the stable UMD WebGL build ---
fetch "https://cdn.jsdelivr.net/npm/sigma@2.4.0/build/sigma.min.js" \
  "$VD/sigma/sigma.min.js"
fetch "https://cdn.jsdelivr.net/npm/sigma@2.4.0/LICENSE.txt" \
  "$VD/licenses/LICENSE-sigma.txt" || \
fetch "https://cdn.jsdelivr.net/npm/sigma@2.4.0/LICENSE" \
  "$VD/licenses/LICENSE-sigma.txt" || true

# --- d3-force + deps (UMD from d3-* packages) ---
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

# Verify globals via node-less file presence
echo "=== vendored ==="
find "$VD" -type f \( -name '*.js' -o -name 'LICENSE*' \) | sort
du -sh "$VD/graphology" "$VD/sigma" "$VD/d3" "$VD/licenses"
