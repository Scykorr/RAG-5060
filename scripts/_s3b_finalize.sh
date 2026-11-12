#!/usr/bin/env bash
set -euo pipefail
cd "/mnt/d/RAG 5060"
python3 scripts/_copy_s3b_shots.py || true
python3 <<'PY'
from pathlib import Path
root=Path('apps/web/vendor')
files=['sigma/sigma.min.js','d3/d3-force.min.js','d3/d3-quadtree.min.js','d3/d3-dispatch.min.js','d3/d3-timer.min.js','d3/d3.min.js.removed-3b','sigma/plugins/node-border.esm.js','sigma/plugins/edge-curve.esm.js']
print('file\tbytes')
for f in files:
 p=root/f
 print(f'{f}\t{p.stat().st_size if p.exists() else "MISSING"}')
slim=[f for f in files if f.startswith('d3/d3-') and not f.endswith('removed-3b')]
print('slim_d3_total', sum((root/f).stat().st_size for f in slim if (root/f).exists()))
PY
ls -la artifacts/20261112-graph-s3b/screenshots 2>/dev/null || true
