# Offline graph vendor libraries

One-time online fetch via `scripts/_vendor_graph_libs_3b.sh` / `_vendor_graph_libs_3b_finish.sh`.
Runtime SPA loads only these local files — no CDN.

| Package | Version | Role | File | sha256 (prefix) |
| --- | --- | --- | --- | --- |
| graphology | 0.25.4 | Graph data model | `graphology/graphology.umd.min.js` → `window.graphology` | `641ea047…` |
| sigma | **3.0.2** | WebGL renderer (UMD → `window.Sigma`) | `sigma/sigma.min.js` | `be6f790d…` |
| @sigma/node-border | 3.0.0 | Optional ESM (theme borders) | `sigma/plugins/node-border.esm.js` | `327f599b…` |
| @sigma/edge-curve | 3.0.0 | Optional ESM | `sigma/plugins/edge-curve.esm.js` | `6f23a0e0…` |
| d3-dispatch | 3.0.1 | Force dependency | `d3/d3-dispatch.min.js` | `94b3bbdb…` |
| d3-timer | 3.0.1 | Force dependency | `d3/d3-timer.min.js` | `911ceda3…` |
| d3-quadtree | 3.0.1 | Force dependency | `d3/d3-quadtree.min.js` | `57e2ad12…` |
| d3-force | 3.0.0 | Layout (`forceSimulation` …) | `d3/d3-force.min.js` → extends `window.d3` | `1e07b473…` |
| cytoscape | (legacy) | Previous engine; unused by GraphView | `cytoscape.min.js` | — |

Full digests: `SHA256SUMS.txt`.

## Size before → after (slice 3b step 0)

| Path | Before | After |
| --- | --- | --- |
| `sigma/sigma.min.js` | ~180 KB (sigma **2.4.0**) | **184 KB** (sigma **3.0.2**) |
| `d3/d3.min.js` | **276 KB** loaded at runtime | **removed from runtime** (kept as `d3.min.js.removed-3b` for size note) |
| `d3/{dispatch,timer,quadtree,force}.min.js` | present but unused | **~28 KB** total, loaded instead |

## Licenses

See `licenses/`:

- graphology — MIT
- sigma / @sigma/* — MIT
- d3-* — ISC

## Load order (UMD)

```html
<script src="/vendor/graphology/graphology.umd.min.js"></script>
<script src="/vendor/sigma/sigma.min.js"></script>
<script src="/vendor/d3/d3-dispatch.min.js"></script>
<script src="/vendor/d3/d3-timer.min.js"></script>
<script src="/vendor/d3/d3-quadtree.min.js"></script>
<script src="/vendor/d3/d3-force.min.js"></script>
<script src="/js/graph/graph-view.js"></script>
```

**Note:** There is a newer sigma 4.x line on npm; this project vendors **sigma 3.0.2** per slice 3b (MIT UMD + EdgeArrowProgram / labelGrid / forceLabel). All sigma API usage stays inside `js/graph/graph-view.js`.
