# Offline graph vendor libraries

One-time online fetch via `scripts/_vendor_graph_libs.sh` (+ `d3.min.js`). Runtime SPA loads only these local files — no CDN.

| Package | Version | Role | File |
| --- | --- | --- | --- |
| graphology | 0.25.4 | Graph data model | `graphology/graphology.umd.min.js` → `window.graphology` |
| sigma | 2.4.0 | WebGL renderer | `sigma/sigma.min.js` → `window.Sigma` |
| d3 | 7.9.0 | Browser UMD with `forceSimulation` / `forceManyBody` / `forceLink` / `forceCenter` | `d3/d3.min.js` → `window.d3` |
| d3-force | 3.0.0 | Source dependency (docs); use via `d3.min.js` in browser | `d3/d3-force.min.js` |
| d3-quadtree | 3.0.1 | Force dependency (source) | `d3/d3-quadtree.min.js` |
| d3-dispatch | 3.0.1 | Force dependency (source) | `d3/d3-dispatch.min.js` |
| d3-timer | 3.0.1 | Force dependency (source) | `d3/d3-timer.min.js` |
| cytoscape | (legacy) | Previous Graph Explorer engine; kept until migration complete | `cytoscape.min.js` |

**Note on sigma version:** there is no published **sigma.js v4**. We vendor **sigma 2.4.0** (stable UMD WebGL + graphology). Coordinates are written by d3-force into graphology; sigma only paints.

## Licenses

See `licenses/`:

- graphology — MIT
- sigma — MIT
- d3 / d3-* — ISC

## Load order (UMD)

```html
<script src="/vendor/graphology/graphology.umd.min.js"></script>
<script src="/vendor/sigma/sigma.min.js"></script>
<script src="/vendor/d3/d3.min.js"></script>
<script src="/js/graph/graph-view.js"></script>
```
