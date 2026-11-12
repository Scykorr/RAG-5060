# Slice 3b — Obsidian Graph polish (after 2d)

**Run-id:** `20261112-graph-s3b`  
**Date:** 2026-11-12  
**Branch commits:** `334f195` (step 0) → `e46363b` (step 1) → `83839db` (steps 2–4) + step 5–6 follow-up  
**Preflight:** 2d PASS (`1471c11`); API restart after accidental rebuild hang; `GET /api/graph/overview?view=levels` → 30 nodes, theme labels from 2d, top-level `theme:8` / label «Без темы» / `attrs.kind=unthemed`.

## Status: PASS (with noted gaps)

UI work stayed inside `apps/web/js/graph/graph-view.js` (+ shell/settings/i18n/CSS). No new runtime npm/pip. Vendor offline only.

### Steps

| Step | Result | Notes |
| --- | --- | --- |
| 0 sigma 3 + slim d3 | PASS | `sigma@3.0.2` UMD; d3-force + dispatch/timer/quadtree; full `d3.min.js` renamed `.removed-3b` |
| 1 arrows / hover / contrast | PASS | `EdgeArrowProgram`; node/edge reducers; custom hover plate from CSS vars; theme `setSetting` without view destroy; min edge ≥1 px; zIndex + opaque dim blend |
| 2 Obsidian look | PASS | Smaller √degree sizes; default theme palette; grey unthemed; top-k edges (default 4); real crumb labels; local center accent |
| 3 layout | PASS | forceX/Y pull; topic→theme soft pull; fit padding for toolbar/settings |
| 3.5 node drag | PASS | downNode + moveBody `preventSigmaDefault`; fx/fy; alphaTarget 0.3; click vs drag threshold 3px; grab/grabbing; double-click unpin; persist via `onDragEnd` |
| 4 settings panel | PASS | Obsidian column: label left / toggle right; ↺ reset; RU edge labels via i18n; groups intact |
| 5 perf fixture | PASS* | Offline `graph-perf-fixture.js` (not SPA-loaded). 1500/5000 ≥50 FPS; 3000 hover ~37 FPS → `hideEdgesOnMove` for ≥1200 nodes |
| 6 report + knowledge | PASS | This file + S62/A27/ERRORS + ROADMAP |

### Vendor sizes

| File | Bytes |
| --- | ---: |
| `sigma/sigma.min.js` (3.0.2) | 186853 |
| `d3/d3.min.js.removed-3b` (old full) | 279706 |
| `d3/d3-force.min.js` | 8300 |
| `d3/d3-quadtree.min.js` | 5279 |
| `d3/d3-dispatch.min.js` | 1901 |
| `d3/d3-timer.min.js` | 1947 |
| slim d3 stack total | ~17427 |
| `sigma/plugins/node-border.esm.js` | (vendored, optional) |
| `sigma/plugins/edge-curve.esm.js` | (vendored, optional) |

**Delta:** SPA no longer loads ~280 KB full d3; loads ~17 KB force stack + sigma 3 UMD.

### Performance (DevTools / GraphPerfFixture)

| Graph | firstPaintMs | settleMs | FPS hover | FPS pan |
| --- | ---: | ---: | ---: | ---: |
| 1500 / 5000 | 61 | 2067 | 60 | 59 |
| 3000 / 10000 | 103 | 2659 | 37 | 56 |

Mitigation for large graphs: `hideEdgesOnMove: true` when `nodes.length >= 1200`; stop simulation at α&lt;0.01 (already).

### Obsidian correspondence

| Obsidian feature | Status |
| --- | --- |
| Force layout + drag nodes | done |
| Hover neighbors + labels | done |
| Theme/light colors live | done |
| Arrows on directed edges | done |
| Color groups / by theme | done |
| Filters + toggles panel | done |
| Top-k / min edge weight | done |
| Local graph center | done |
| Screenshots of WebGL | done with `preserveDrawingBuffer` |
| Edge curve plugin | vendored, not default (straight + arrow) |
| Search animation timelapse | partial (existing animate button) |

### Checks

- External network requests for graph libs: **0** (vendor only).
- Settings survive reload: localStorage `rag5060.graph.settings.v1.*`.
- Positions survive reload: `rag5060.graph.pos.v1.*` + drag `onDragEnd`.
- Hash back navigation: existing `history.back` / crumb handlers.
- Contrast hover plates: dark `#dcddde` on `#2a2a2a`; light `#222` on `#fff` (target ≥4.5:1).

### Screenshots

Under `artifacts/20261112-graph-s3b/screenshots/` (overview dark + settings, light chrome, etc.). Capture requires `webGLContextOptions.preserveDrawingBuffer=true`.

### Known follow-ups

- 3000-node hover FPS &lt; 50 without further LOD.
- `@sigma/node-border` used via UMD `createNodeBorderProgram` when available; ESM plugin kept for reference.
- Accidental `POST /api/graph/rebuild` from UI ↻ can block the single API worker — prefer soft reload; restart API if health hangs.
