/**
 * Graph view adapter — graphology + sigma 3 (WebGL) + d3-force.
 * Swap the engine here without touching the Obsidian-like UI shell.
 *
 * API: create, setData, updateData, setForces, setDisplay, highlight, focusNode, destroy
 */
/* global graphology, Sigma, d3 */
(function (global) {
  "use strict";

  const DIRECTED_EDGE_KINDS = new Set([
    "refers_to",
    "work_refers_to",
    "mentions_author",
    "wrote",
    "contains",
    "part_of",
    "EXPLICIT_REFERENCE",
  ]);

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

  /** Resolve renderer programs from the sigma 3 UMD global. */
  function sigmaPrograms(S) {
    const pick = (name) => (S && S[name]) || (S && S.default && S.default[name]) || null;
    return {
      EdgeArrowProgram: pick("EdgeArrowProgram"),
      EdgeRectangleProgram: pick("EdgeRectangleProgram") || pick("EdgeLineProgram"),
      NodeCircleProgram: pick("NodeCircleProgram"),
      createNodeBorderProgram: pick("createNodeBorderProgram"),
    };
  }

  function cssVar(name, fallback) {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (_) {
      return fallback;
    }
  }

  function isLightTheme() {
    return document.documentElement.getAttribute("data-theme") === "light";
  }

  function readThemePalette(display) {
    const light = isLightTheme();
    const d = display || {};
    return {
      light,
      bg: d.bg || cssVar("--graph-bg", light ? "#ffffff" : "#1e1e1e"),
      accent: d.accent || cssVar("--graph-accent", "#7f6df2"),
      nodeColor: d.nodeColor || (light ? "#666666" : "#999999"),
      edgeColor: d.edgeColor || (light ? "rgba(154,154,154,0.42)" : "rgba(138,138,138,0.38)"),
      labelColor: d.labelColor || (light ? "#222222" : "#dcddde"),
      hoverPlate: light ? "rgba(255,255,255,0.92)" : "rgba(42,42,42,0.9)",
      hoverText: light ? "#222222" : "#dcddde",
      dimEdge: light ? "rgba(154,154,154,0.18)" : "rgba(138,138,138,0.14)",
    };
  }

  function parseHex(hex) {
    const h = String(hex || "").replace("#", "").trim();
    if (h.length === 3) {
      return {
        r: parseInt(h[0] + h[0], 16),
        g: parseInt(h[1] + h[1], 16),
        b: parseInt(h[2] + h[2], 16),
      };
    }
    if (h.length >= 6) {
      return {
        r: parseInt(h.slice(0, 2), 16),
        g: parseInt(h.slice(2, 4), 16),
        b: parseInt(h.slice(4, 6), 16),
      };
    }
    return null;
  }

  function toHex(r, g, b) {
    const c = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
    return `#${c(r)}${c(g)}${c(b)}`;
  }

  /** Mix node color toward background; amount=0.15 → ~15% original chroma left. */
  function blendTowardBg(color, bg, amount) {
    const a = Math.max(0, Math.min(1, amount == null ? 0.15 : amount));
    const c = parseHex(color) || parseHex("#999999");
    const b = parseHex(bg) || { r: 30, g: 30, b: 30 };
    return toHex(c.r * a + b.r * (1 - a), c.g * a + b.g * (1 - a), c.b * a + b.b * (1 - a));
  }

  function relativeLuminance(hex) {
    const c = parseHex(hex);
    if (!c) return 0;
    const f = (v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }

  function contrastRatio(fg, bg) {
    const L1 = relativeLuminance(fg);
    const L2 = relativeLuminance(bg);
    const hi = Math.max(L1, L2);
    const lo = Math.min(L1, L2);
    return (hi + 0.05) / (lo + 0.05);
  }

  function drawRoundedRect(ctx, x, y, w, h, r) {
    const rr = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + rr, y);
    ctx.arcTo(x + w, y, x + w, y + h, rr);
    ctx.arcTo(x + w, y + h, x, y + h, rr);
    ctx.arcTo(x, y + h, x, y, rr);
    ctx.arcTo(x, y, x + w, y, rr);
    ctx.closePath();
  }

  function makeDrawNodeLabel(getPalette) {
    return function drawNodeLabel(context, data, settings) {
      if (!data.label) return;
      const p = getPalette();
      const size = settings.labelSize || 12;
      const font = `${settings.labelWeight || "400"} ${size}px ${settings.labelFont || "sans-serif"}`;
      context.font = font;
      context.fillStyle = p.labelColor;
      context.textAlign = "center";
      context.textBaseline = "top";
      context.fillText(data.label, data.x, data.y + data.size + 4);
    };
  }

  function makeDrawNodeHover(getPalette) {
    return function drawNodeHover(context, data, settings) {
      if (!data.label) return;
      const p = getPalette();
      const size = data.size || 4;
      // Accent ring — no white halo around the disc.
      context.beginPath();
      context.arc(data.x, data.y, size + 3, 0, Math.PI * 2);
      context.strokeStyle = p.accent;
      context.lineWidth = 2;
      context.stroke();

      const labelSize = settings.labelSize || 12;
      const font = `600 ${labelSize}px ${settings.labelFont || "sans-serif"}`;
      context.font = font;
      const padX = 6;
      const padY = 4;
      const metrics = context.measureText(data.label);
      const tw = metrics.width + padX * 2;
      const th = labelSize + padY * 2;
      const lx = data.x - tw / 2;
      const ly = data.y + size + 6;
      drawRoundedRect(context, lx, ly, tw, th, 4);
      context.fillStyle = p.hoverPlate;
      context.fill();
      context.fillStyle = p.hoverText;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(data.label, data.x, ly + th / 2);
    };
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
        labelColor: opts.labelColor || "#dcddde",
        bg: opts.bg || "#1e1e1e",
        dimOpacity: 0.15,
      },
      highlight: { focus: null, neighbors: null },
      highlightFade: 0,
      _fadeRaf: 0,
      pinned: new Set(),
      onClick: opts.onClick || null,
      onHover: opts.onHover || null,
      onDragEnd: opts.onDragEnd || null,
      onReady: opts.onReady || null,
      raf: 0,
      destroyed: false,
    };

    const programs = sigmaPrograms(S);
    state._sigmaPrograms = programs;
    const edgeProgramClasses = {};
    if (programs.EdgeRectangleProgram) edgeProgramClasses.line = programs.EdgeRectangleProgram;
    if (programs.EdgeArrowProgram) edgeProgramClasses.arrow = programs.EdgeArrowProgram;
    const nodeProgramClasses = {};
    if (programs.NodeCircleProgram) nodeProgramClasses.circle = programs.NodeCircleProgram;
    if (typeof programs.createNodeBorderProgram === "function") {
      try {
        nodeProgramClasses.bordered = programs.createNodeBorderProgram({
          borders: [{ size: { value: 2, absoluteSizeOnScreen: true }, color: { attribute: "borderColor" } }],
        });
      } catch (_) {
        /* keep circle-only */
      }
    }

    const getPalette = () => readThemePalette(state.display);

    state.sigma = new S(state.graph, container, {
      renderLabels: true,
      labelFont: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
      labelSize: 12,
      labelWeight: "400",
      labelColor: { color: getPalette().labelColor },
      labelGridCellSize: 80,
      labelDensity: 0.7,
      defaultEdgeType: "line",
      defaultNodeType: "circle",
      defaultNodeColor: state.display.nodeColor,
      defaultEdgeColor: state.display.edgeColor,
      edgeProgramClasses: Object.keys(edgeProgramClasses).length ? edgeProgramClasses : undefined,
      nodeProgramClasses: Object.keys(nodeProgramClasses).length ? nodeProgramClasses : undefined,
      zIndex: true,
      allowInvalidContainer: true,
      defaultDrawNodeLabel: makeDrawNodeLabel(getPalette),
      defaultDrawNodeHover: makeDrawNodeHover(getPalette),
      nodeReducer: (node, data) => reduceNode(state, node, data),
      edgeReducer: (edge, data) => reduceEdge(state, edge, data),
    });

    applySigmaTheme(state);
    wireSigmaEvents(state);
    applyForces(state);
    if (state.onReady) state.onReady({ kind: "sigma", webgl: true });
    try {
      global.__graphViewDebug = () => ({
        nodes: state.graph ? state.graph.order : 0,
        edges: state.graph ? state.graph.size : 0,
        camera: state.sigma && state.sigma.getCamera ? state.sigma.getCamera().getState() : null,
        sample: (() => {
          const out = [];
          if (!state.graph) return out;
          state.graph.forEachNode((id, a) => {
            if (out.length < 5) out.push({ id, x: a.x, y: a.y, size: a.size, color: a.color, label: a.label });
          });
          return out;
        })(),
      });
    } catch (_) {}

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
          applySigmaTheme(state);
          applyEdgeTypes(state);
          refreshBaseAttrs(state);
          if (state.sigma) state.sigma.refresh();
        }
      },
      fit: () => fitView(state),
      zoomBy: (factor) => zoomBy(state, factor),
      highlight: (h) => {
        animateHighlight(state, h || {});
        if (state.kind === "fallback") renderFallbackList(state);
      },
      focusNode: (id) => focusNode(state, id),
      getPositions: () => getPositions(state),
      setPositions: (pos) => setPositions(state, pos),
      pinNode: (id, pinned) => {
        if (pinned) state.pinned.add(id);
        else {
          state.pinned.delete(id);
          if (state.sim) {
            const n = state.sim.nodes().find((nn) => nn.id === id);
            if (n) {
              n.fx = null;
              n.fy = null;
            }
          }
        }
      },
      reheat: () => {
        if (state.sim) state.sim.alpha(0.7).restart();
      },
      resize: () => {
        if (state.sigma && state.sigma.resize) state.sigma.resize();
      },
      themeContrast: () => {
        const p = readThemePalette(state.display);
        return {
          darkOrLight: p.light ? "light" : "dark",
          hoverTextOnPlate: contrastRatio(p.hoverText, p.light ? "#ffffff" : "#2a2a2a"),
          labelOnBg: contrastRatio(p.labelColor, p.bg),
        };
      },
      destroy: () => destroy(state),
    };
  }

  function applySigmaTheme(state) {
    if (!state.sigma || !state.sigma.setSetting) return;
    const p = readThemePalette(state.display);
    state.display.bg = p.bg;
    state.display.accent = p.accent;
    state.display.labelColor = p.labelColor;
    state.display.nodeColor = state.display.nodeColor || p.nodeColor;
    state.display.edgeColor = state.display.edgeColor || p.edgeColor;
    const getPalette = () => readThemePalette(state.display);
    state.sigma.setSetting("labelColor", { color: p.labelColor });
    state.sigma.setSetting("defaultNodeColor", p.nodeColor);
    state.sigma.setSetting("defaultEdgeColor", p.edgeColor);
    state.sigma.setSetting("defaultDrawNodeLabel", makeDrawNodeLabel(getPalette));
    state.sigma.setSetting("defaultDrawNodeHover", makeDrawNodeHover(getPalette));
    const thr = state.display.labelThreshold != null ? state.display.labelThreshold : 0.55;
    state.sigma.setSetting("labelRenderedSizeThreshold", 4 + (1 - thr) * 28);
  }

  function animateHighlight(state, h) {
    state.highlight = h || {};
    if (state.kind !== "sigma") return;
    const target = state.highlight.focus ? 1 : 0;
    if (state._fadeRaf) cancelAnimationFrame(state._fadeRaf);
    const start = state.highlightFade || 0;
    const t0 = performance.now();
    const dur = 180;
    const tick = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      const ease = t * (2 - t);
      state.highlightFade = start + (target - start) * ease;
      if (state.sigma) state.sigma.refresh();
      if (t < 1) state._fadeRaf = requestAnimationFrame(tick);
      else state._fadeRaf = 0;
    };
    state._fadeRaf = requestAnimationFrame(tick);
  }

  function reduceNode(state, node, data) {
    const hi = state.highlight || {};
    const fade = state.highlightFade || 0;
    const focus = hi.focus;
    const nbrs = hi.neighbors instanceof Set ? hi.neighbors : new Set(hi.neighbors || []);
    const dimming = !!(focus || nbrs.size) && fade > 0.01;
    const p = readThemePalette(state.display);
    const baseColor = data.color || state.display.nodeColor || p.nodeColor;

    const isTheme = data.nodeKind === "theme";
    const isLocalCenter = state.display.localCenterId && node === state.display.localCenterId;
    if (!dimming) {
      return {
        ...data,
        zIndex: isTheme || isLocalCenter ? 1 : data.zIndex || 0,
        forceLabel: !!(isTheme || isLocalCenter),
        labelWeight: isTheme || isLocalCenter ? "600" : data.labelWeight || "400",
      };
    }

    const hot = node === focus || nbrs.has(node) || isLocalCenter;
    if (hot) {
      return {
        ...data,
        color: isLocalCenter ? p.accent : baseColor,
        forceLabel: true,
        zIndex: 2,
        highlighted: true,
        labelWeight: isTheme || isLocalCenter ? "600" : "400",
      };
    }

    // fade=1 → keep ~15% of node color mixed into bg (opaque, not alpha).
    return {
      ...data,
      color: blendTowardBg(baseColor, p.bg, 1 - fade * 0.85),
      label: null,
      forceLabel: false,
      zIndex: 0,
      highlighted: false,
    };
  }

  function reduceEdge(state, edge, data) {
    const g = state.graph;
    const hi = state.highlight || {};
    const fade = state.highlightFade || 0;
    const focus = hi.focus;
    const nbrs = hi.neighbors instanceof Set ? hi.neighbors : new Set(hi.neighbors || []);
    const dimming = !!(focus || nbrs.size) && fade > 0.01;
    const p = readThemePalette(state.display);
    const extremities = g.extremities(edge);
    const source = extremities[0];
    const target = extremities[1];
    const weight = data.weight || 0.2;
    const scale = state.display.edgeScale || 1;
    let size = Math.max(1, weight * 2.2 * scale);
    let color = state.display.edgeColor || p.edgeColor;
    let zIndex = 0;
    const directed = !!(data.directed || DIRECTED_EDGE_KINDS.has(data.edgeKind));
    let type = data.type || "line";
    if (state.display.arrows && directed && state._sigmaPrograms && state._sigmaPrograms.EdgeArrowProgram) {
      type = "arrow";
    } else {
      type = "line";
    }

    if (dimming) {
      const hot =
        source === focus ||
        target === focus ||
        (nbrs.has(source) && (target === focus || nbrs.has(target))) ||
        (nbrs.has(target) && source === focus);
      if (hot) {
        color = p.accent;
        size = Math.max(1.5, Math.min(2.5, size * 1.6));
        zIndex = 2;
      } else {
        color = p.dimEdge;
        size = Math.max(1, size * 0.85);
        zIndex = 0;
      }
    } else {
      size = Math.max(1, size);
    }

    return { ...data, color, size, type, zIndex };
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
    return DIRECTED_EDGE_KINDS.has(type);
  }

  function edgeGraphType(state, e) {
    if (state.display.arrows && e.directed && state._sigmaPrograms && state._sigmaPrograms.EdgeArrowProgram) {
      return "arrow";
    }
    return "line";
  }

  function nodeRadius(n, display) {
    const deg = Math.max(0, n.degree || 0);
    const scale = display.nodeScale || 1;
    const kind = n.type || "work";
    // Obsidian-like: works ~2–10px, topics a bit larger, themes up to ~14–18.
    const bySqrt = 2 + Math.sqrt(deg + 1) * 1.35;
    let r;
    if (kind === "theme") r = Math.min(18, Math.max(10, 8 + Math.sqrt(deg + 1) * 1.8));
    else if (kind === "topic") r = Math.min(12, Math.max(4, 3.2 + Math.sqrt(deg + 1) * 1.4));
    else r = Math.min(10, Math.max(2, bySqrt));
    if (display.localCenterId && n.id === display.localCenterId) r = Math.max(r * 1.45, r + 3);
    return Math.max(2, r * scale);
  }

  function applyEdgeTypes(state) {
    if (state.kind !== "sigma" || !state.graph) return;
    state.graph.forEachEdge((eid, attrs) => {
      const directed = !!(attrs.directed || DIRECTED_EDGE_KINDS.has(attrs.edgeKind));
      const type =
        state.display.arrows && directed && state._sigmaPrograms && state._sigmaPrograms.EdgeArrowProgram
          ? "arrow"
          : "line";
      state.graph.setEdgeAttribute(eid, "type", type);
    });
  }

  function refreshBaseAttrs(state) {
    if (state.kind !== "sigma") return;
    const g = state.graph;
    const d = state.display;
    const p = readThemePalette(d);
    g.forEachNode((id, attrs) => {
      const n = state.nodes.get(id);
      const color = (n && n.color) || d.nodeColor || p.nodeColor;
      g.setNodeAttribute(id, "color", color);
      g.setNodeAttribute(
        id,
        "size",
        nodeRadius(n || { size: 1, degree: attrs.degree || 0, type: attrs.nodeKind || "work" }, d)
      );
    });
    g.forEachEdge((eid, attrs) => {
      const e = state.edges.get(eid);
      const size = Math.max(1, (e ? e.weight : 0.2) * 2.2 * (d.edgeScale || 1));
      g.setEdgeAttribute(eid, "color", d.edgeColor || p.edgeColor);
      g.setEdgeAttribute(eid, "size", size);
      g.setEdgeAttribute(eid, "type", edgeGraphType(state, e || { directed: attrs.directed }));
    });
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
    nodes.forEach((n, i) => {
      const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2;
      const r = 40 + (i % 7) * 18;
      const isTheme = n.type === "theme";
      const bordered = isTheme && state._sigmaPrograms && state.sigma && state.sigma.getSetting && (state.sigma.getSetting("nodeProgramClasses") || {}).bordered;
      g.addNode(n.id, {
        label: n.label,
        x: n.x != null ? n.x : Math.cos(angle) * r + (Math.random() - 0.5) * 8,
        y: n.y != null ? n.y : Math.sin(angle) * r + (Math.random() - 0.5) * 8,
        size: nodeRadius(n, state.display),
        color: n.color || state.display.nodeColor,
        nodeKind: n.type,
        degree: n.degree,
        ghost: n.ghost,
        external: n.external,
        zIndex: isTheme ? 1 : 0,
        type: bordered ? "bordered" : "circle",
        borderColor: isTheme ? state.display.accent || "#7f6df2" : undefined,
        forceLabel: isTheme,
        labelWeight: isTheme ? "600" : "400",
        themeId: (n.raw && (n.raw.theme_id || (n.raw.attrs && n.raw.attrs.theme_id))) || null,
      });
    });
    state.edges.forEach((e) => {
      if (!g.hasNode(e.source) || !g.hasNode(e.target)) return;
      if (g.hasEdge(e.id)) return;
      try {
        g.addEdgeWithKey(e.id, e.source, e.target, {
          weight: e.weight,
          size: Math.max(1, e.weight * 2 * (state.display.edgeScale || 1)),
          color: state.display.edgeColor,
          edgeKind: e.type,
          directed: e.directed,
          type: edgeGraphType(state, e),
          zIndex: 0,
        });
      } catch (_) {
        /* parallel edge */
      }
    });
    applyForces(state, true);
    refreshBaseAttrs(state);
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
          zIndex: 0,
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
            size: Math.max(1, e.weight * 2 * (state.display.edgeScale || 1)),
            color: state.display.edgeColor,
            edgeKind: e.type,
            directed: e.directed,
            type: edgeGraphType(state, e),
            zIndex: 0,
          });
        } catch (_) {}
      }
    });
    applyForces(state, true);
    refreshBaseAttrs(state);
    if (state.sigma) state.sigma.refresh();
  }

  function applyForces(state, reheat) {
    if (state.kind !== "sigma" || typeof d3 === "undefined") return;
    const g = state.graph;
    const nodes = [];
    const byId = new Map();
    g.forEachNode((id, attrs) => {
      const n = {
        id,
        x: attrs.x || 0,
        y: attrs.y || 0,
        fx: state.pinned.has(id) ? attrs.x : null,
        fy: state.pinned.has(id) ? attrs.y : null,
        nodeKind: attrs.nodeKind,
        themeId: attrs.themeId,
      };
      nodes.push(n);
      byId.set(id, n);
    });
    const links = [];
    g.forEachEdge((eid, attrs, source, target) => {
      links.push({ id: eid, source, target, weight: attrs.weight || 0.1 });
    });

    if (state.sim) state.sim.stop();
    const f = state.forces;
    const pull = f.pull != null ? f.pull : 0.04;
    const sim = d3
      .forceSimulation(nodes)
      .force("center", d3.forceCenter(0, 0).strength(f.center))
      .force("x", d3.forceX(0).strength(pull))
      .force("y", d3.forceY(0).strength(pull))
      .force("charge", d3.forceManyBody().strength(f.charge))
      .force(
        "link",
        d3
          .forceLink(links)
          .id((d) => d.id)
          .distance((d) => {
            const a = byId.get(d.source.id || d.source);
            const b = byId.get(d.target.id || d.target);
            if (a && b && ((a.nodeKind === "theme" && b.nodeKind === "topic") || (b.nodeKind === "theme" && a.nodeKind === "topic"))) {
              return Math.max(36, (f.distance || 80) * 0.55);
            }
            return f.distance;
          })
          .strength((d) => Math.min(1, (d.weight || 0.1) * f.link))
      )
      .alpha(reheat ? 0.6 : 0.25)
      .alphaDecay(0.028)
      .on("tick", () => {
        if (state.destroyed) return;
        // Soft pull of topics toward their theme center (cluster look).
        nodes.forEach((n) => {
          if (n.nodeKind !== "topic" || !n.themeId) return;
          const tid = String(n.themeId).startsWith("theme:") ? String(n.themeId) : `theme:${n.themeId}`;
          const theme = byId.get(tid) || byId.get(String(n.themeId));
          if (!theme || n.fx != null) return;
          n.vx = (n.vx || 0) + (theme.x - n.x) * 0.02;
          n.vy = (n.vy || 0) + (theme.y - n.y) * 0.02;
        });
        nodes.forEach((n) => {
          if (!g.hasNode(n.id)) return;
          if (state.pinned.has(n.id)) return;
          g.setNodeAttribute(n.id, "x", n.x);
          g.setNodeAttribute(n.id, "y", n.y);
        });
        if (state.sigma) state.sigma.refresh();
        if (state.sim && state.sim.alpha() < 0.01) state.sim.stop();
      });
    state.sim = sim;
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
    const padL = 24;
    const padR = 96; // zoom buttons
    const padT = 56; // top icon cluster
    const padB = 40;
    const settingsOpen = !document.getElementById("graph-settings-panel")?.classList.contains("hidden");
    const padRightExtra = settingsOpen ? 320 : 0;
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const w = Math.max(40, maxX - minX);
    const h = Math.max(40, maxY - minY);
    const vw = Math.max(120, (state.container.clientWidth || 800) - padL - padR - padRightExtra);
    const vh = Math.max(120, (state.container.clientHeight || 600) - padT - padB);
    const ratio = Math.max(w / vw, h / vh) * 1.35;
    cam.animate({ x: cx, y: cy, ratio: Math.max(0.08, ratio) }, { duration: 280 });
  }

  function zoomBy(state, factor) {
    if (state.kind !== "sigma" || !state.sigma || !state.sigma.getCamera) return;
    const cam = state.sigma.getCamera();
    const next = Math.min(8, Math.max(0.05, cam.ratio * (factor || 1)));
    cam.animate({ ratio: next }, { duration: 120 });
  }

  function wireSigmaEvents(state) {
    const s = state.sigma;
    if (!s || !s.on) return;
    // Node drag (Obsidian-like): pan only on empty stage; node down pins fx/fy.
    let drag = null;
    let dragMoved = false;
    let suppressClick = false;

    s.on("clickNode", ({ node }) => {
      if (suppressClick) {
        suppressClick = false;
        return;
      }
      if (state.onClick) state.onClick({ id: node, node: state.nodes.get(node) });
    });
    s.on("enterNode", ({ node }) => {
      if (!drag) state.container.style.cursor = "grab";
      const nbrs = new Set();
      state.graph.forEachNeighbor(node, (nb) => nbrs.add(nb));
      animateHighlight(state, { focus: node, neighbors: nbrs });
      if (state.onHover) state.onHover({ id: node, neighbors: [...nbrs] });
    });
    s.on("leaveNode", () => {
      if (!drag) state.container.style.cursor = "";
      animateHighlight(state, {});
      if (state.onHover) state.onHover(null);
    });
    const mouseCaptor = s.getMouseCaptor && s.getMouseCaptor();
    const touchCaptor = s.getTouchCaptor && s.getTouchCaptor();

    const beginDrag = (nodeId, x, y, ev) => {
      if (ev && ev.preventSigmaDefault) ev.preventSigmaDefault();
      drag = { id: nodeId, x0: x, y0: y };
      dragMoved = false;
      suppressClick = false;
      state.pinned.add(nodeId);
      state.container.style.cursor = "grabbing";
      if (state.sim) state.sim.alphaTarget(0.3).restart();
    };
    const moveDrag = (x, y, ev) => {
      if (!drag) return;
      if (ev && ev.preventSigmaDefault) ev.preventSigmaDefault();
      const dx = (x || 0) - (drag.x0 || 0);
      const dy = (y || 0) - (drag.y0 || 0);
      if (Math.hypot(dx, dy) > 3) {
        dragMoved = true;
        suppressClick = true;
      }
      const pos = state.sigma.viewportToGraph ? state.sigma.viewportToGraph({ x, y }) : null;
      if (!pos || !state.graph.hasNode(drag.id)) return;
      state.graph.setNodeAttribute(drag.id, "x", pos.x);
      state.graph.setNodeAttribute(drag.id, "y", pos.y);
      if (state.sim) {
        const n = state.sim.nodes().find((nn) => nn.id === drag.id);
        if (n) {
          n.fx = pos.x;
          n.fy = pos.y;
          n.x = pos.x;
          n.y = pos.y;
        }
      }
      state.sigma.refresh();
    };
    const endDrag = () => {
      if (!drag) return;
      const id = drag.id;
      drag = null;
      state.container.style.cursor = "";
      if (state.sim) {
        const n = state.sim.nodes().find((nn) => nn.id === id);
        if (n) {
          n.fx = n.x;
          n.fy = n.y;
        }
        state.sim.alphaTarget(0);
      }
      if (dragMoved && state.onDragEnd) state.onDragEnd({ id });
    };

    s.on("downNode", (e) => {
      const pt = e.event || {};
      beginDrag(e.node, pt.x, pt.y, e);
    });
    if (mouseCaptor) {
      mouseCaptor.on("mousemovebody", (e) => moveDrag(e.x, e.y, e));
      mouseCaptor.on("mouseup", endDrag);
    }
    if (touchCaptor) {
      touchCaptor.on("touchmove", (e) => {
        if (!drag) return;
        const t = (e.touches && e.touches[0]) || e;
        moveDrag(t.x, t.y, e);
      });
      touchCaptor.on("touchup", endDrag);
    }
    s.on("upNode", endDrag);
    s.on("upStage", endDrag);
    s.on("doubleClickNode", ({ node }) => {
      state.pinned.delete(node);
      if (state.sim) {
        const n = state.sim.nodes().find((nn) => nn.id === node);
        if (n) {
          n.fx = null;
          n.fy = null;
        }
      }
      if (state.onDragEnd) state.onDragEnd({ id: node, unpinned: true });
    });
  }

  function focusNode(state, id) {
    if (state.kind === "fallback") return;
    if (!state.graph.hasNode(id) || !state.sigma) return;
    const attrs = state.graph.getNodeAttributes(id);
    if (state.sigma.getCamera) {
      const cam = state.sigma.getCamera();
      cam.animate({ x: attrs.x, y: attrs.y, ratio: cam.ratio }, { duration: 280 });
    }
    const nbrs = new Set();
    state.graph.forEachNeighbor(id, (nb) => nbrs.add(nb));
    animateHighlight(state, { focus: id, neighbors: nbrs });
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
    if (state._fadeRaf) cancelAnimationFrame(state._fadeRaf);
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
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  global.GraphView = { create };
})(typeof window !== "undefined" ? window : globalThis);
