/**
 * Offline perf fixture for GraphView (not loaded by the SPA).
 * Open via ?perf=1 harness or evaluate in DevTools:
 *   GraphPerfFixture.run(GraphView, {nodes:1500, edges:5000})
 */
(function (global) {
  "use strict";

  function makeGraph(nNodes, nEdges) {
    const nodes = [];
    const edges = [];
    for (let i = 0; i < nNodes; i++) {
      const type = i % 40 === 0 ? "theme" : i % 7 === 0 ? "topic" : "work";
      nodes.push({
        id: `n${i}`,
        label: type === "theme" ? `Тема ${i}` : type === "topic" ? `Тематика ${i}` : `Узел ${i}`,
        type,
        size: 1,
        degree: 0,
        color: type === "theme" ? "#7f6df2" : undefined,
        attrs: type === "topic" ? { theme_id: `n${Math.floor(i / 40) * 40}` } : {},
      });
    }
    for (let i = 0; i < nEdges; i++) {
      const a = Math.floor(Math.random() * nNodes);
      let b = Math.floor(Math.random() * nNodes);
      if (b === a) b = (b + 1) % nNodes;
      edges.push({
        id: `e${i}`,
        source: `n${a}`,
        target: `n${b}`,
        type: i % 5 === 0 ? "refers_to" : "work_lex_similar",
        weight: Math.random(),
      });
      nodes[a].degree += 1;
      nodes[b].degree += 1;
    }
    return { nodes, edges };
  }

  function measureFps(ms) {
    return new Promise((resolve) => {
      let frames = 0;
      const t0 = performance.now();
      const tick = (t) => {
        frames += 1;
        if (t - t0 < ms) requestAnimationFrame(tick);
        else resolve(frames / ((t - t0) / 1000));
      };
      requestAnimationFrame(tick);
    });
  }

  async function run(GraphView, opts) {
    const o = opts || {};
    const nNodes = o.nodes || 1500;
    const nEdges = o.edges || 5000;
    const host = o.host || document.getElementById("graph-canvas") || document.body;
    const tBuild0 = performance.now();
    const data = makeGraph(nNodes, nEdges);
    const tBuild1 = performance.now();
    const view = GraphView.create(host, { accent: "#7f6df2" });
    const tPaint0 = performance.now();
    view.setData(data);
    view.fit();
    const tPaint1 = performance.now();
    // wait layout to settle
    await new Promise((r) => setTimeout(r, o.settleMs || 2500));
    const tStable = performance.now();
    const fpsHover = await measureFps(1000);
    const fpsPan = await measureFps(1000);
    const out = {
      nodes: nNodes,
      edges: nEdges,
      buildMs: Math.round(tBuild1 - tBuild0),
      firstPaintMs: Math.round(tPaint1 - tPaint0),
      settleMs: Math.round(tStable - tPaint0),
      fpsHover: Math.round(fpsHover),
      fpsPan: Math.round(fpsPan),
    };
    if (o.destroy !== false) view.destroy();
    return out;
  }

  global.GraphPerfFixture = { makeGraph, run, measureFps };
})(typeof window !== "undefined" ? window : globalThis);
