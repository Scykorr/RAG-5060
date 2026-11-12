/**
 * Graph view adapter — graphology + sigma (WebGL) + d3-force.
 * Swap the engine here without touching the Obsidian-like UI shell.
 *
 * API: create, setData, updateData, setForces, setDisplay, highlight, focusNode, destroy
 */
/* global graphology, Sigma, d3 */
(function (global) {
  "use strict";

  function webglOk() {
    try {
      const c = document.createElement("canvas");
      return !!(c.getContext("webgl") || c.getContext("experimental-webgl"));
    } catch (_) {
      return false;
    }
  }

  function GraphCtor() {
    if (typeof graphology === "function") return graphology;
    if (graphology && typeof graphology.Graph === "function") return graphology.Graph;
    return null;
  }

  function SigmaCtor() {
    if (typeof Sigma === "function") return Sigma;
    if (Sigma && typeof Sigma.Sigma === "function") return Sigma.Sigma;
    if (Sigma && typeof Sigma.default === "function") return Sigma.default;
    return null;
  }

  function create(container, options) {
    const opts = options || {};
    if (!container) throw new Error("graph-view: container required");

    if (!webglOk()) {
      return createFallback(container, opts);
    }
    const G = GraphCtor();
    const S = SigmaCtor();
    if (!G || !S || typeof d3 === "undefined" || !d3.forceSimulation) {
      return createFallback(container, opts);
    }

    const state = {
      kind: "sigma",
      container,
      graph: new G(),
      sigma: null,
      sim: null,
      nodes: new Map(),
      edges: new Map(),
      forces: {
        center: 0.05,
        charge: -180,
        link: 0.4,
        distance: 80,
      },
      display: {
        nodeScale: 1,
        edgeScale: 1,
        arrows: true,
        labelThreshold: 0.55,
        accent: opts.accent || "#7f6df2",
        nodeColor: opts.nodeColor || "#999999",
        edgeColor: opts.edgeColor || "rgba(153,153,153,0.4)",
        dimOpacity: 0.15,
      },
      highlight: { focus: null, neighbors: null },
      pinned: new Set(),
      onClick: opts.onClick || null,
      onHover: opts.onHover || null,
      onReady: opts.onReady || null,
      raf: 0,
      destroyed: false,
    };

    // Default styles via node/edge reducer
    state.sigma = new S(state.graph, container, {
      renderLabels: true,
      labelFont: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
      labelSize: 12,
      labelWeight: "400",
      labelColor: { color: opts.labelColor || "#d7e0f2" },
      defaultEdgeType: "line",
      defaultNodeColor: state.display.nodeColor,
      defaultEdgeColor: state.display.edgeColor,
      zIndex: true,
      allowInvalidContainer: true,
    });

    wireSigmaEvents(state);
    applyForces(state);
    if (state.onReady) state.onReady({ kind: "sigma", webgl: true });

    return api(state);
  }

  function createFallback(container, opts) {
    container.innerHTML = "";
    const box = document.createElement("div");
    box.className = "graph-fallback";
    box.innerHTML =
      "<p><strong>WebGL недоступен</strong> — граф в режиме списка (без Sigma).</p>" +
      '<ul class="graph-fallback-list"></ul>';
    container.appendChild(box);
    const list = box.querySelector(".graph-fallback-list");
    const state = {
      kind: "fallback",
      container,
      list,
      nodes: new Map(),
      edges: new Map(),
      forces: {},
      display: {},
      highlight: {},
      onClick: opts.onClick || null,
      destroyed: false,
    };
    if (opts.onReady) opts.onReady({ kind: "fallback", webgl: false });
    return api(state);
  }

  function api(state) {
    return {
      kind: () => state.kind,
      setData: (payload) => setData(state, payload),
      updateData: (payload) => updateData(state, payload),
      setForces: (f) => {
        Object.assign(state.forces, f || {});
        if (state.kind === "sigma") applyForces(state, true);
      },
      setDisplay: (d) => {
        Object.assign(state.display, d || {});
        if (state.kind === "sigma") {
          if (state.sigma && state.sigma.setSetting) {
            const thr = state.display.labelThreshold != null ? state.display.labelThreshold : 0.55;
            // Higher threshold → fewer labels (Obsidian text-fade).
            state.sigma.setSetting("labelRenderedSizeThreshold", 4 + (1 - thr) * 28);
          }
          refreshStyles(state);
        }
      },
      fit: () => fitView(state),
      zoomBy: (factor) => zoomBy(state, factor),
      highlight: (h) => {
        state.highlight = h || {};
        if (state.kind === "sigma") refreshStyles(state);
        if (state.kind === "fallback") renderFallbackList(state);
      },
      focusNode: (id) => focusNode(state, id),
      getPositions: () => getPositions(state),
      setPositions: (pos) => setPositions(state, pos),
      pinNode: (id, pinned) => {
        if (pinned) state.pinned.add(id);
        else state.pinned.delete(id);
      },
      reheat: () => {
        if (state.sim) state.sim.alpha(0.7).restart();
      },
      resize: () => {
        if (state.sigma && state.sigma.resize) state.sigma.resize();
      },
      destroy: () => destroy(state),
    };
  }

  function normalizeNodes(raw) {
    return (raw || []).map((n) => ({
      id: String(n.id),
      label: String(n.label || n.id),
      type: String(n.type || "work"),
      size: Number(n.size) || 1,
      degree: Number((n.attrs && n.attrs.degree) || n.degree || 0),
      color: n.color,
      x: n.x,
      y: n.y,
      external: !!(n.attrs && (n.attrs.external || n.attrs.neighbor || n.attrs.ghost)),
      ghost: !!(n.attrs && (n.attrs.external || n.attrs.ghost)),
      raw: n,
    }));
  }

  function normalizeEdges(raw) {
    return (raw || []).map((e, i) => ({
      id: String(e.id || `e${i}`),
      source: String(e.source),
      target: String(e.target),
      type: String(e.type || "link"),
      weight: Number(e.weight) || 0.1,
      directed: isDirected(e.type),
      raw: e,
    }));
  }

  function isDirected(type) {
    return (
      type === "refers_to" ||
      type === "work_refers_to" ||
      type === "mentions_author" ||
      type === "wrote" ||
      type === "contains" ||
      type === "part_of" ||
      type === "EXPLICIT_REFERENCE"
    );
  }

  function nodeRadius(n, display) {
    const deg = Math.max(0, n.degree || 0);
    const base = 3 + Math.sqrt(deg + Math.max(1, n.size)) * 2.2;
    const typeBoost = n.type === "theme" ? 2.2 : n.type === "topic" ? 1.5 : 1;
    return Math.max(3, base * typeBoost * (display.nodeScale || 1));
  }

  function setData(state, payload) {
    const nodes = normalizeNodes(payload && payload.nodes);
    const edges = normalizeEdges(payload && payload.edges);
    state.nodes.clear();
    state.edges.clear();
    nodes.forEach((n) => state.nodes.set(n.id, n));
    edges.forEach((e) => {
      if (state.nodes.has(e.source) && state.nodes.has(e.target)) state.edges.set(e.id, e);
    });

    if (state.kind === "fallback") {
      renderFallbackList(state);
      return;
    }

    const g = state.graph;
    g.clear();
    const w = state.container.clientWidth || 800;
    const h = state.container.clientHeight || 600;
    nodes.forEach((n, i) => {
      const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2;
      const r = 40 + (i % 7) * 18;
      g.addNode(n.id, {
        label: n.label,
        x: n.x != null ? n.x : Math.cos(angle) * r + (Math.random() - 0.5) * 8,
        y: n.y != null ? n.y : Math.sin(angle) * r + (Math.random() - 0.5) * 8,
        size: nodeRadius(n, state.display),
        color: n.color || state.display.nodeColor,
        // Do NOT set graphology/sigma `type` — that selects a WebGL program (circle/…).
        nodeKind: n.type,
        degree: n.degree,
        ghost: n.ghost,
        external: n.external,
      });
    });
    state.edges.forEach((e) => {
      if (!g.hasNode(e.source) || !g.hasNode(e.target)) return;
      if (g.hasEdge(e.id)) return;
      try {
        g.addEdgeWithKey(e.id, e.source, e.target, {
          weight: e.weight,
          size: Math.max(0.4, e.weight * 2 * (state.display.edgeScale || 1)),
          color: state.display.edgeColor,
          edgeKind: e.type,
          directed: e.directed,
        });
      } catch (_) {
        /* parallel edge */
      }
    });
    applyForces(state, true);
    refreshStyles(state);
    if (state.sigma && state.sigma.refresh) state.sigma.refresh();
  }

  function updateData(state, payload) {
    if (state.kind === "fallback") {
      setData(state, {
        nodes: [...(payload.nodes || [])],
        edges: [...(payload.edges || [])],
      });
      return;
    }
    const g = state.graph;
    const incoming = normalizeNodes(payload && payload.nodes);
    const nextIds = new Set(incoming.map((n) => n.id));
    const keepPos = getPositions(state);

    // Remove gone nodes
    [...state.nodes.keys()].forEach((id) => {
      if (!nextIds.has(id) && g.hasNode(id)) {
        try {
          g.dropNode(id);
        } catch (_) {}
        state.nodes.delete(id);
      }
    });

    incoming.forEach((n) => {
      state.nodes.set(n.id, n);
      if (g.hasNode(n.id)) {
        g.setNodeAttribute(n.id, "label", n.label);
        g.setNodeAttribute(n.id, "size", nodeRadius(n, state.display));
        g.setNodeAttribute(n.id, "ghost", n.ghost);
      } else {
        const nbrs = [];
        (payload.edges || []).forEach((e) => {
          if (e.source === n.id && keepPos[e.target]) nbrs.push(keepPos[e.target]);
          if (e.target === n.id && keepPos[e.source]) nbrs.push(keepPos[e.source]);
        });
        let x = (Math.random() - 0.5) * 40;
        let y = (Math.random() - 0.5) * 40;
        if (nbrs.length) {
          x = nbrs.reduce((s, p) => s + p.x, 0) / nbrs.length + (Math.random() - 0.5) * 24;
          y = nbrs.reduce((s, p) => s + p.y, 0) / nbrs.length + (Math.random() - 0.5) * 24;
        } else if (keepPos[n.id]) {
          x = keepPos[n.id].x;
          y = keepPos[n.id].y;
        }
        g.addNode(n.id, {
          label: n.label,
          x,
          y,
          size: nodeRadius(n, state.display),
          color: n.color || state.display.nodeColor,
          nodeKind: n.type,
          degree: n.degree,
          ghost: n.ghost,
        });
      }
    });

    state.edges.clear();
    const wantEdges = normalizeEdges(payload && payload.edges);
    const wantEdgeIds = new Set(wantEdges.map((e) => e.id));
    g.forEachEdge((eid) => {
      if (!wantEdgeIds.has(eid)) {
        try {
          g.dropEdge(eid);
        } catch (_) {}
      }
    });
    wantEdges.forEach((e) => {
      if (!g.hasNode(e.source) || !g.hasNode(e.target)) return;
      state.edges.set(e.id, e);
      if (!g.hasEdge(e.id)) {
        try {
          g.addEdgeWithKey(e.id, e.source, e.target, {
            weight: e.weight,
            size: Math.max(0.4, e.weight * 2 * (state.display.edgeScale || 1)),
            color: state.display.edgeColor,
            edgeKind: e.type,
            directed: e.directed,
          });
        } catch (_) {}
      }
    });
    applyForces(state, true);
    refreshStyles(state);
  }

  function applyForces(state, reheat) {
    if (state.kind !== "sigma" || typeof d3 === "undefined") return;
    const g = state.graph;
    const nodes = [];
    g.forEachNode((id, attrs) => {
      nodes.push({
        id,
        x: attrs.x || 0,
        y: attrs.y || 0,
        fx: state.pinned.has(id) ? attrs.x : null,
        fy: state.pinned.has(id) ? attrs.y : null,
      });
    });
    const links = [];
    g.forEachEdge((eid, attrs, source, target) => {
      links.push({ id: eid, source, target, weight: attrs.weight || 0.1 });
    });

    if (state.sim) state.sim.stop();
    const f = state.forces;
    state.sim = d3
      .forceSimulation(nodes)
      .force("center", d3.forceCenter(0, 0).strength(f.center))
      .force("charge", d3.forceManyBody().strength(f.charge))
      .force(
        "link",
        d3
          .forceLink(links)
          .id((d) => d.id)
          .distance(f.distance)
          .strength((d) => Math.min(1, (d.weight || 0.1) * f.link))
      )
      .alpha(reheat ? 0.6 : 0.25)
      .alphaDecay(0.028)
      .on("tick", () => {
        if (state.destroyed) return;
        nodes.forEach((n) => {
          if (!g.hasNode(n.id)) return;
          if (state.pinned.has(n.id)) return;
          g.setNodeAttribute(n.id, "x", n.x);
          g.setNodeAttribute(n.id, "y", n.y);
        });
        if (state.sigma) state.sigma.refresh();
        if (state.sim && state.sim.alpha() < 0.01) state.sim.stop();
      });
  }

  function fitView(state) {
    if (state.kind !== "sigma" || !state.sigma || !state.graph) return;
    const g = state.graph;
    let minX = Infinity,
      minY = Infinity,
      maxX = -Infinity,
      maxY = -Infinity,
      n = 0;
    g.forEachNode((_id, a) => {
      minX = Math.min(minX, a.x);
      minY = Math.min(minY, a.y);
      maxX = Math.max(maxX, a.x);
      maxY = Math.max(maxY, a.y);
      n += 1;
    });
    if (!n || !state.sigma.getCamera) return;
    const cam = state.sigma.getCamera();
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const w = Math.max(40, maxX - minX);
    const h = Math.max(40, maxY - minY);
    const vw = state.container.clientWidth || 800;
    const vh = state.container.clientHeight || 600;
    const ratio = Math.max(w / vw, h / vh) * 1.25;
    cam.animate({ x: cx, y: cy, ratio: Math.max(0.08, ratio) }, { duration: 280 });
  }

  function zoomBy(state, factor) {
    if (state.kind !== "sigma" || !state.sigma || !state.sigma.getCamera) return;
    const cam = state.sigma.getCamera();
    const next = Math.min(8, Math.max(0.05, cam.ratio * (factor || 1)));
    cam.animate({ ratio: next }, { duration: 120 });
  }

  function refreshStyles(state) {
    if (state.kind !== "sigma") return;
    const g = state.graph;
    const d = state.display;
    const hi = state.highlight || {};
    const focus = hi.focus;
    const nbrs = hi.neighbors instanceof Set ? hi.neighbors : new Set(hi.neighbors || []);
    const dimming = !!(focus || nbrs.size);

    g.forEachNode((id, attrs) => {
      const n = state.nodes.get(id);
      let color = (n && n.color) || d.nodeColor;
      let opacity = attrs.ghost ? 0.3 : 1;
      if (dimming) {
        const hot = id === focus || nbrs.has(id);
        opacity = hot ? 1 : d.dimOpacity;
        if (hot) color = d.accent;
      }
      g.setNodeAttribute(id, "color", color);
      g.setNodeAttribute(
        id,
        "size",
        nodeRadius(n || { size: 1, degree: attrs.degree || 0, type: attrs.nodeKind || "work" }, d)
      );
      // sigma 2 uses color alpha via hex; approximate dim by blending toward bg
      if (opacity < 1 && !String(color).startsWith("#7f")) {
        g.setNodeAttribute(id, "color", mixAlpha(color, opacity));
      }
    });

    g.forEachEdge((eid, attrs, source, target) => {
      const e = state.edges.get(eid);
      let color = d.edgeColor;
      let size = Math.max(0.35, (e ? e.weight : 0.2) * 2.2 * (d.edgeScale || 1));
      if (dimming) {
        const hot =
          source === focus ||
          target === focus ||
          (nbrs.has(source) && (target === focus || nbrs.has(target))) ||
          (nbrs.has(target) && source === focus);
        if (hot) {
          color = d.accent;
          size *= 1.8;
        } else {
          color = mixAlpha("#999999", d.dimOpacity);
        }
      }
      g.setEdgeAttribute(eid, "color", color);
      g.setEdgeAttribute(eid, "size", size);
    });
    if (state.sigma) state.sigma.refresh();
  }

  function mixAlpha(hex, a) {
    // Return rgba string for partial opacity
    const h = String(hex || "#999999").replace("#", "");
    if (h.length === 3) {
      const r = parseInt(h[0] + h[0], 16);
      const g = parseInt(h[1] + h[1], 16);
      const b = parseInt(h[2] + h[2], 16);
      return `rgba(${r},${g},${b},${a})`;
    }
    if (h.length >= 6) {
      const r = parseInt(h.slice(0, 2), 16);
      const g = parseInt(h.slice(2, 4), 16);
      const b = parseInt(h.slice(4, 6), 16);
      return `rgba(${r},${g},${b},${a})`;
    }
    return hex;
  }

  function wireSigmaEvents(state) {
    const s = state.sigma;
    if (!s || !s.on) return;
    s.on("clickNode", ({ node }) => {
      if (state.onClick) state.onClick({ id: node, node: state.nodes.get(node) });
    });
    s.on("enterNode", ({ node }) => {
      const nbrs = new Set();
      state.graph.forEachNeighbor(node, (nb) => nbrs.add(nb));
      state.highlight = { focus: node, neighbors: nbrs };
      refreshStyles(state);
      if (state.onHover) state.onHover({ id: node, neighbors: [...nbrs] });
    });
    s.on("leaveNode", () => {
      state.highlight = {};
      refreshStyles(state);
      if (state.onHover) state.onHover(null);
    });
    // Drag via camera / custom: sigma 2 has no built-in node drag; use mouse down + reheat
    let drag = null;
    s.on("downNode", (e) => {
      drag = { id: e.node };
      state.pinned.add(e.node);
      if (state.sim) state.sim.alphaTarget(0.3).restart();
    });
    const onMove = (ev) => {
      if (!drag || !state.sigma) return;
      const pos = state.sigma.viewportToGraph
        ? state.sigma.viewportToGraph({ x: ev.offsetX, y: ev.offsetY })
        : null;
      if (!pos) return;
      const g = state.graph;
      if (!g.hasNode(drag.id)) return;
      g.setNodeAttribute(drag.id, "x", pos.x);
      g.setNodeAttribute(drag.id, "y", pos.y);
      if (state.sim) {
        const n = state.sim.nodes().find((x) => x.id === drag.id);
        if (n) {
          n.fx = pos.x;
          n.fy = pos.y;
          n.x = pos.x;
          n.y = pos.y;
        }
      }
      state.sigma.refresh();
    };
    const onUp = () => {
      if (!drag) return;
      const id = drag.id;
      drag = null;
      if (state.sim) {
        const n = state.sim.nodes().find((x) => x.id === id);
        if (n) {
          n.fx = n.x;
          n.fy = n.y;
        }
        state.sim.alphaTarget(0);
      }
      // stay pinned where dropped (Obsidian-like until «Перестроить»)
    };
    state.container.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    state._dragClean = () => {
      state.container.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }

  function focusNode(state, id) {
    if (state.kind === "fallback") return;
    if (!state.graph.hasNode(id) || !state.sigma) return;
    const attrs = state.graph.getNodeAttributes(id);
    if (state.sigma.getCamera) {
      const cam = state.sigma.getCamera();
      cam.animate({ x: attrs.x, y: attrs.y, ratio: cam.ratio }, { duration: 280 });
    }
    state.highlight = { focus: id, neighbors: new Set(state.graph.neighbors(id)) };
    refreshStyles(state);
  }

  function getPositions(state) {
    const out = {};
    if (state.kind !== "sigma") return out;
    state.graph.forEachNode((id, attrs) => {
      out[id] = { x: attrs.x, y: attrs.y };
    });
    return out;
  }

  function setPositions(state, pos) {
    if (state.kind !== "sigma" || !pos) return;
    Object.keys(pos).forEach((id) => {
      if (!state.graph.hasNode(id)) return;
      state.graph.setNodeAttribute(id, "x", pos[id].x);
      state.graph.setNodeAttribute(id, "y", pos[id].y);
    });
    applyForces(state, false);
    if (state.sim) state.sim.alpha(0.08).restart();
    if (state.sigma) state.sigma.refresh();
  }

  function renderFallbackList(state) {
    if (!state.list) return;
    const items = [...state.nodes.values()];
    state.list.innerHTML = items
      .slice(0, 500)
      .map(
        (n) =>
          `<li data-id="${escapeAttr(n.id)}"><button type="button" class="linkish">${escapeHtml(
            n.label
          )}</button> <span class="muted">${escapeHtml(n.type)}</span></li>`
      )
      .join("");
    state.list.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.closest("li")?.getAttribute("data-id");
        if (id && state.onClick) state.onClick({ id, node: state.nodes.get(id) });
      });
    });
  }

  function destroy(state) {
    state.destroyed = true;
    if (state.sim) state.sim.stop();
    if (state._dragClean) state._dragClean();
    if (state.sigma) {
      try {
        state.sigma.kill();
      } catch (_) {}
    }
    state.sigma = null;
    state.graph = null;
    state.container.innerHTML = "";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, "&quot;");
  }

  global.GraphView = { create, webglOk };
})(typeof window !== "undefined" ? window : globalThis);
