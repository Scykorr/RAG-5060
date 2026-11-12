#!/usr/bin/env bash
set -euo pipefail
cd "/mnt/d/RAG 5060"
git add \
  apps/web/js/graph/graph-view.js \
  apps/web/js/graph/graph-perf-fixture.js \
  artifacts/20261112-graph-s3b \
  docs/SKILLS.md docs/ABILITIES.md docs/ERRORS.md docs/progress.md \
  ROADMAP_local_RAG_RTX5060Ti_16GB.md \
  "Обучение/docs/SKILLS.md" "Обучение/docs/ABILITIES.md" "Обучение/docs/ERRORS.md" \
  scripts/_s3b_finalize.sh scripts/_copy_s3b_shots.py scripts/_commit_3b_step56.sh || true

# Prefer Unicode path if present
git add Обучение/docs/SKILLS.md Обучение/docs/ABILITIES.md Обучение/docs/ERRORS.md 2>/dev/null || true

git commit -m "Slice 3b steps 5–6: perf fixture/hideEdgesOnMove, report, S62/A27 knowledge and ROADMAP F15.6d."
git status -sb | head -15
git log -5 --oneline
