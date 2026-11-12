/**
 * Obsidian-style graph shell: levels, hash routes, settings, side panel.
 * Rendering goes through GraphView only.
 */
/* global GraphView, GraphSettings, GraphI18n */
(function (global) {
  "use strict";

  const t = (k, v) => (GraphI18n && GraphI18n.t ? GraphI18n.t(k, v) : k);

  const STRUCTURAL = new Set([
    "has_topic",
    "has_work",
    "theme_link",
    "topic_link",
    "topic_knn",
    "in_theme",
    "part_of",
  ]);

  const NODE_TYPE_KEYS = ["theme", "topic", "work", "document", "author", "collection"];
  const EDGE_TYPE_KEYS = [
    "has_topic",
    "has_work",
    "theme_link",
    "topic_link",
    "topic_knn",
    "work_lex_similar",
    "lex_similar",
    "work_refers_to",
    "refers_to",
    "work_shared_entity",
    "shared_entity",
    "mentions_author",
    "contains",
    "wrote",
    "part_of",
    "in_theme",
    "in_collection",
  ];

  const state = {
    wired: false,
    visible: false,
    view: null,
    route: { level: "overview", id: null },
    settings: null,
    settingsLevel: "global",
    raw: null,
    etag: null,
    limit: 1500,
    searchHits: new Set(),
    searchTimer: null,
    posTimer: null,
    animTimer: null,
    selectedId: null,
    tenantId: "default",
    busy: false,
    t0: 0,
  };

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function themeColors() {
    const light = document.documentElement.getAttribute("data-theme") === "light";
    return {
      accent: cssVar("--graph-accent", "#7f6df2"),
      nodeColor: light ? "#666666" : "#999999",
      edgeColor: light ? "rgba(154,154,154,0.42)" : "rgba(138,138,138,0.38)",
      labelColor: light ? "#222222" : "#dcddde",
      bg: cssVar("--graph-bg", light ? "#ffffff" : "#1e1e1e"),
    };
  }

  function api(path, opts) {
    return window.api(path, opts);
  }

  function tenant() {
    return (window.state && window.state.user && (window.state.user.tenant_id || window.state.user.tenantId)) || "default";
  }

  function settingsBucket() {
    return state.route.level === "local" ? "local" : "global";
  }

  function loadSettings() {
    state.tenantId = tenant();
    state.settingsLevel = settingsBucket();
    state.settings = GraphSettings.load(state.tenantId, state.settingsLevel);
  }

  function saveSettings() {
    GraphSettings.save(state.tenantId, state.settingsLevel, state.settings);
  }

  function posCacheKey() {
    const r = state.route;
    return { level: r.level, id: r.id || "root" };
  }

  function persistPositionsSoon() {
    if (!state.view) return;
    clearTimeout(state.posTimer);
    state.posTimer = setTimeout(() => {
      const k = posCacheKey();
      GraphSettings.savePositions(state.tenantId, k.level, k.id, state.view.getPositions());
    }, 800);
  }

  function parseHash() {
    const h = (location.hash || "").replace(/^#/, "");
    if (!h.startsWith("graph")) return null;
    const parts = h.split("/").filter(Boolean);
    // graph | graph/overview | graph/theme/5 | graph/topic/10 | graph/local/work:x
    if (parts.length <= 1 || parts[1] === "overview") return { level: "overview", id: null };
    if (parts[1] === "theme" && parts[2]) return { level: "theme", id: decodeURIComponent(parts[2]) };
    if (parts[1] === "topic" && parts[2]) return { level: "topic", id: decodeURIComponent(parts[2]) };
    if (parts[1] === "local" && parts[2]) return { level: "local", id: decodeURIComponent(parts.slice(2).join("/")) };
    if (parts[1] === "node" && parts[2]) return { level: "local", id: decodeURIComponent(parts.slice(2).join("/")) };
    return { level: "overview", id: null };
  }

  function writeHash(route, replace) {
    let h = "#graph/overview";
    if (route.level === "theme" && route.id) h = `#graph/theme/${encodeURIComponent(route.id)}`;
    else if (route.level === "topic" && route.id) h = `#graph/topic/${encodeURIComponent(route.id)}`;
    else if (route.level === "local" && route.id) h = `#graph/local/${encodeURIComponent(route.id)}`;
    if (replace) history.replaceState(null, "", h);
    else if (location.hash !== h) location.hash = h;
  }

  let lastTap = { id: null, t: 0 };

  function handleNodeClick(id, node) {
    const now = Date.now();
    if (lastTap.id === id && now - lastTap.t < 350) {
      lastTap = { id: null, t: 0 };
      const raw = (node && node.raw) || node || {};
      const typ = raw.type || (node && node.type);
      if (typ === "theme") navigate({ level: "theme", id });
      else if (typ === "topic") navigate({ level: "topic", id });
      return;
    }
    lastTap = { id, t: now };
    state.selectedId = id;
    openPanel(id, node);
  }

  function ensureView() {
    const host = document.getElementById("graph-canvas");
    if (!host) return null;
    if (state.view) {
      state.view.resize();
      return state.view;
    }
    const colors = themeColors();
    host.style.background = colors.bg;
    state.view = GraphView.create(host, {
      accent: colors.accent,
      nodeColor: colors.nodeColor,
      edgeColor: colors.edgeColor,
      labelColor: colors.labelColor,
      onClick: ({ id, node }) => handleNodeClick(id, node),
      onDragEnd: () => persistPositionsSoon(),
    });
    return state.view;
  }

  async function fetchLevel(route, limit) {
    const lim = limit || state.limit;
    let path = `/api/graph/overview?view=levels&limit=${lim}`;
    if (route.level === "theme" && route.id) path = `/api/graph/themes/${encodeURIComponent(route.id)}?limit=${lim}`;
    else if (route.level === "topic" && route.id) path = `/api/graph/topics/${encodeURIComponent(route.id)}?limit=${lim}`;
    else if (route.level === "local" && route.id) {
      const L = state.settings.local || {};
      const dirs = [];
      if (L.incoming !== false) dirs.push("in");
      if (L.outgoing !== false) dirs.push("out");
      if (L.undirected !== false) dirs.push("undirected");
      const depth = Math.max(1, Math.min(3, Number(L.depth) || 2));
      path = `/api/graph/local/${encodeURIComponent(route.id)}?depth=${depth}&directions=${encodeURIComponent(
        dirs.join(",") || "in,out,undirected"
      )}&limit=${lim}`;
    }
    const headers = {};
    if (state.etag && state.route.level === route.level && state.route.id === route.id) {
      headers["If-None-Match"] = state.etag;
    }
    const res = await fetch(path, { credentials: "include", headers });
    if (res.status === 304) return { unchanged: true };
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const detail = err.detail;
      throw new Error(
        (typeof detail === "string" && detail) ||
          (detail && detail.message) ||
          err.message ||
          `HTTP ${res.status}`
      );
    }
    const body = await res.json();
    return { body, etag: res.headers.get("ETag") };
  }

  function edgeTypeAllowed(type, filters) {
    const et = filters.edgeTypes || {};
    if (et[type] === true) return true;
    // aliases
    if (type === "lex_similar" && et.work_lex_similar) return true;
    if (type === "work_lex_similar" && et.lex_similar) return true;
    if (type === "refers_to" && et.work_refers_to) return true;
    if (type === "work_refers_to" && et.refers_to) return true;
    if (type === "shared_entity" && et.work_shared_entity) return true;
    if (type === "work_shared_entity" && et.shared_entity) return true;
    if (STRUCTURAL.has(type) && (et.has_topic || et.has_work || et.theme_link || et.topic_link || et.topic_knn)) {
      // structural master: if any structural toggle on and type is structural member default-on
      if (et[type] === false) return false;
      if (et[type] === true) return true;
      // fallback: structural types enabled when their specific key missing but "structural" intent via defaults
      return et[type] !== false;
    }
    return et[type] === true;
  }

  function matchGroupQuery(node, query) {
    const q = String(query || "").trim();
    if (!q) return false;
    const attrs = node.attrs || {};
    const m = q.match(/^(author|theme|topic|lang|kind|collection|path):\s*(.+)$/i);
    if (m) {
      const key = m[1].toLowerCase();
      const val = m[2].trim().toLowerCase();
      if (key === "author") {
        const blob = `${attrs.author_key || ""} ${attrs.author_ru || ""} ${attrs.author || ""}`.toLowerCase();
        return blob.includes(val);
      }
      if (key === "theme") return String(node.theme_id || attrs.theme_id || "") === val || String(node.id) === `theme:${val}` || String(node.id) === val;
      if (key === "topic") return String(node.topic_id || attrs.topic_id || "") === val || String(node.id) === `topic:${val}` || String(node.id) === val;
      if (key === "lang") return String(node.lang || attrs.lang || "unknown").toLowerCase() === val;
      if (key === "kind") return String(node.doc_kind || attrs.doc_kind || "unknown").toLowerCase() === val;
      if (key === "collection") return String(node.collection || "").toLowerCase().includes(val);
      if (key === "path") return String(attrs.filename || attrs.path || "").toLowerCase().includes(val);
    }
    const blob = `${node.label || ""} ${attrs.filename || ""} ${attrs.author_ru || ""}`.toLowerCase();
    return blob.includes(q.toLowerCase());
  }

  function colorForNode(node, settings) {
    const groups = settings.groups || [];
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i];
      if (g && g.query && matchGroupQuery(node, g.query)) return g.color || GraphSettings.THEME_PALETTE[i % GraphSettings.THEME_PALETTE.length];
    }
    return null;
  }

  function ensureThemeColorGroups(raw, settings) {
    if (!settings.display || settings.display.colorByThemes === false) return;
    if (settings.groups && settings.groups.length) return;
    const themes = (raw.nodes || []).filter((n) => n.type === "theme");
    if (!themes.length) return;
    settings.groups = themes.map((n, i) => {
      const kind = (n.attrs && n.attrs.kind) || "";
      const grey = kind === "unthemed" || /без темы|прочее/i.test(String(n.label || ""));
      return {
        query: `theme:${String(n.id).replace(/^theme:/, "")}`,
        color: grey ? "#888888" : GraphSettings.THEME_PALETTE[i % GraphSettings.THEME_PALETTE.length],
      };
    });
  }

  function themeColorForId(themeKey, settings) {
    const id = String(themeKey || "").replace(/^theme:/, "");
    const groups = settings.groups || [];
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i];
      if (!g || !g.query) continue;
      const m = String(g.query).match(/^theme:\s*(.+)$/i);
      if (m && String(m[1]).replace(/^theme:/, "") === id) return g.color;
    }
    return null;
  }

  function defaultNodeColor(n, settings, colors) {
    const typ = String(n.type || "work");
    const kind = (n.attrs && n.attrs.kind) || "";
    if (kind === "unthemed" || /без темы|прочее/i.test(String(n.label || ""))) return "#888888";
    if (typ === "theme") {
      return themeColorForId(n.id, settings) || GraphSettings.THEME_PALETTE[0];
    }
    const tid = n.theme_id || (n.attrs && n.attrs.theme_id);
    if (tid) return themeColorForId(tid, settings) || colors.nodeColor;
    return colors.nodeColor;
  }

  function topKEdges(edges, maxPerNode) {
    const k = Math.max(0, Number(maxPerNode) || 0);
    if (!k) return edges;
    const byNode = new Map();
    edges.forEach((e) => {
      const w = Number(e.weight) || 0;
      if (!byNode.has(e.source)) byNode.set(e.source, []);
      if (!byNode.has(e.target)) byNode.set(e.target, []);
      byNode.get(e.source).push(e);
      byNode.get(e.target).push(e);
    });
    const keep = new Set();
    byNode.forEach((list) => {
      list
        .slice()
        .sort((a, b) => (Number(b.weight) || 0) - (Number(a.weight) || 0))
        .slice(0, k)
        .forEach((e) => keep.add(e));
    });
    return edges.filter((e) => keep.has(e));
  }

  function filterPayload(raw, settings) {
    const f = settings.filters;
    const colors = themeColors();
    ensureThemeColorGroups(raw, settings);
    const nodes = [];
    const degree = new Map();
    (raw.edges || []).forEach((e) => {
      degree.set(e.source, (degree.get(e.source) || 0) + 1);
      degree.set(e.target, (degree.get(e.target) || 0) + 1);
    });

    (raw.nodes || []).forEach((n) => {
      const typ = String(n.type || "work");
      if (f.nodeTypes[typ] === false) return;
      const ghost = !!(n.attrs && (n.attrs.ghost || n.attrs.external));
      if (f.onlyExisting && ghost) return;
      const lang = String(n.lang || (n.attrs && n.attrs.lang) || "unknown").toLowerCase();
      const langKey = ["ru", "en", "mixed"].includes(lang) ? lang : lang === "unknown" ? "unknown" : "other";
      if (f.langs && f.langs[langKey] === false) return;
      const kind = String(n.doc_kind || (n.attrs && n.attrs.doc_kind) || "unknown").toLowerCase();
      if (f.kinds && f.kinds[kind] === false) return;
      const themed = !!(n.theme_id || (n.attrs && n.attrs.theme_id));
      if (!f.unthemed && !themed && typ !== "theme" && typ !== "topic") return;
      const deg = degree.get(n.id) || 0;
      if (!f.orphans && deg === 0 && typ !== "theme") return;
      const colored = colorForNode(n, settings) || defaultNodeColor(n, settings, colors);
      nodes.push({
        ...n,
        color: colored || undefined,
        degree: (n.attrs && n.attrs.degree) || deg,
      });
    });
    const ids = new Set(nodes.map((n) => n.id));
    let edges = (raw.edges || []).filter((e) => {
      if (!ids.has(e.source) || !ids.has(e.target)) return false;
      if (!edgeTypeAllowed(e.type, f)) return false;
      if (Number(e.weight || 0) < Number(f.minWeight || 0)) return false;
      return true;
    });
    if (state.route && state.route.level === "overview") {
      edges = topKEdges(edges, f.maxEdgesPerNode != null ? f.maxEdgesPerNode : 4);
    }
    return { nodes, edges, meta: raw.meta || {} };
  }

  function labelForNodeId(id) {
    if (!id) return "";
    const fromRaw = (state.raw && (state.raw.nodes || []).find((n) => String(n.id) === String(id))) || null;
    if (fromRaw && fromRaw.label) return String(fromRaw.label);
    return String(id);
  }

  function renderBreadcrumbs() {
    const el = document.getElementById("graph-crumbs");
    if (!el) return;
    const r = state.route;
    const parts = [`<button type="button" class="graph-crumb" data-go="overview">${t("overview")}</button>`];
    if (r.level === "theme" && r.id) {
      parts.push(
        `<span class="graph-crumb-sep">›</span><button type="button" class="graph-crumb" data-go="theme" data-id="${esc(r.id)}">${esc(
          labelForNodeId(r.id.startsWith("theme:") ? r.id : `theme:${r.id}`)
        )}</button>`
      );
    }
    if (r.level === "topic" && r.id) {
      const topicNode = (state.raw && (state.raw.nodes || []).find((n) => String(n.id) === String(r.id) || String(n.id) === `topic:${r.id}`)) || null;
      const themeId = topicNode && (topicNode.theme_id || (topicNode.attrs && topicNode.attrs.theme_id));
      if (themeId) {
        parts.push(
          `<span class="graph-crumb-sep">›</span><button type="button" class="graph-crumb" data-go="theme" data-id="${esc(themeId)}">${esc(
            labelForNodeId(String(themeId).startsWith("theme:") ? themeId : `theme:${themeId}`)
          )}</button>`
        );
      }
      parts.push(
        `<span class="graph-crumb-sep">›</span><button type="button" class="graph-crumb" data-go="topic" data-id="${esc(r.id)}">${esc(
          labelForNodeId(r.id.startsWith("topic:") ? r.id : `topic:${r.id}`)
        )}</button>`
      );
    }
    if (r.level === "local" && r.id) {
      parts.push(`<span class="graph-crumb-sep">›</span><span class="graph-crumb current">${esc(labelForNodeId(r.id))}</span>`);
    }
    el.innerHTML = parts.join("");
    el.querySelectorAll("[data-go]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const go = btn.getAttribute("data-go");
        if (go === "overview") navigate({ level: "overview", id: null });
        else if (go === "theme") navigate({ level: "theme", id: btn.getAttribute("data-id") });
        else if (go === "topic") navigate({ level: "topic", id: btn.getAttribute("data-id") });
      });
    });
  }

  function setBanner(html, show) {
    const el = document.getElementById("graph-banner");
    if (!el) return;
    el.classList.toggle("hidden", !show);
    if (show) el.innerHTML = html;
  }

  function setTruncated(meta) {
    const el = document.getElementById("graph-truncated");
    if (!el) return;
    if (meta && meta.truncated) {
      el.classList.remove("hidden");
      el.innerHTML = `${t("truncated", { n: (state.raw && state.raw.nodes && state.raw.nodes.length) || 0, m: meta.total_nodes || "?" })} <button type="button" class="btn ghost" id="graph-show-more">${t("showMore")}</button>`;
      document.getElementById("graph-show-more")?.addEventListener("click", () => {
        state.limit = Math.min(3000, Math.max(state.limit, 1500) + 500);
        reload(true);
      });
    } else {
      el.classList.add("hidden");
    }
  }

  function applyToView(filtered, incremental) {
    const view = ensureView();
    if (!view) return;
    if (state.raw) {
      const before = (state.settings.groups || []).length;
      ensureThemeColorGroups(state.raw, state.settings);
      if ((state.settings.groups || []).length !== before) saveSettings();
    }
    const colors = themeColors();
    view.setDisplay({
      nodeScale: state.settings.display.nodeScale,
      edgeScale: state.settings.display.edgeScale,
      arrows: state.settings.display.arrows,
      labelThreshold: state.settings.display.labelThreshold,
      accent: colors.accent,
      nodeColor: colors.nodeColor,
      edgeColor: colors.edgeColor,
      labelColor: colors.labelColor,
      bg: colors.bg,
      localCenterId: state.route.level === "local" ? state.route.id : null,
    });
    view.setForces(state.settings.forces);
    const k = posCacheKey();
    const cached = GraphSettings.loadPositions(state.tenantId, k.level, k.id);
    const nodes = filtered.nodes.map((n) => {
      if (cached && cached[n.id]) {
        return { ...n, x: cached[n.id].x, y: cached[n.id].y };
      }
      return n;
    });
    if (incremental && state.view) view.updateData({ nodes, edges: filtered.edges });
    else view.setData({ nodes, edges: filtered.edges });
    if (cached) view.setPositions(cached);
    persistPositionsSoon();
    if (state.searchHits.size) {
      view.highlight({ focus: null, neighbors: state.searchHits });
    }
  }

  async function reload(force) {
    if (state.busy && !force) return;
    if (state.busy && force) {
      // allow recovery after a failed paint (e.g. sigma program mismatch)
      state.busy = false;
    }
    state.busy = true;
    state.t0 = performance.now();
    const wrap = document.getElementById("graph-stage");
    wrap?.classList.add("is-loading");
    document.getElementById("graph-empty")?.classList.add("hidden");
    try {
      loadSettings();
      const route = state.route;
      const res = await fetchLevel(route, state.limit);
      if (res.unchanged && !force) {
        /* keep */
      } else if (res.body) {
        const prevIds = state.raw ? new Set((state.raw.nodes || []).map((n) => n.id)) : null;
        state.raw = res.body;
        state.etag = res.etag;
        const incremental = !!(prevIds && prevIds.size);
        const filtered = filterPayload(state.raw, state.settings);
        applyToView(filtered, incremental && !force);
        const ms = Math.round(performance.now() - state.t0);
        document.getElementById("graph-status-line").textContent = `${filtered.nodes.length} узлов · ${filtered.edges.length} связей · ${ms} мс`;
        setTruncated(state.raw.meta);
        if (state.raw.meta && state.raw.meta.stale) {
          setBanner(`${t("stale")} <button type="button" class="btn graph-banner-cta" id="graph-banner-rebuild">${t("rebuild")}</button>`, true);
          document.getElementById("graph-banner-rebuild")?.addEventListener("click", () => rebuild());
        } else if (!(state.raw.nodes || []).length) {
          setBanner("", false);
          document.getElementById("graph-empty")?.classList.remove("hidden");
        } else {
          setBanner("", false);
        }
        if (state.settings.display.animate) runTimelapse(filtered.nodes);
      }
      renderBreadcrumbs();
      renderLocalControls();
      syncSettingsForm();
    } catch (e) {
      setBanner(String(e.message || e), true);
      document.getElementById("graph-empty")?.classList.remove("hidden");
      window.toast?.(String(e.message || e), "bad");
    } finally {
      wrap?.classList.remove("is-loading");
      state.busy = false;
      state.view?.resize();
    }
  }

  function runTimelapse(nodes) {
    clearInterval(state.animTimer);
    if (!state.view) return;
    const ordered = [...nodes].sort((a, b) => {
      const ca = Date.parse((a.attrs && a.attrs.created_at) || 0) || 0;
      const cb = Date.parse((b.attrs && b.attrs.created_at) || 0) || 0;
      return ca - cb;
    });
    let i = 0;
    state.view.setData({ nodes: [], edges: [] });
    state.animTimer = setInterval(() => {
      i = Math.min(ordered.length, i + Math.max(1, Math.ceil(ordered.length / 40)));
      const slice = ordered.slice(0, i);
      const ids = new Set(slice.map((n) => n.id));
      const edges = (state.raw.edges || []).filter((e) => ids.has(e.source) && ids.has(e.target));
      const filtered = filterPayload({ nodes: slice, edges, meta: state.raw.meta }, state.settings);
      state.view.updateData(filtered);
      if (i >= ordered.length) clearInterval(state.animTimer);
    }, 120);
  }

  function navigate(route, opts) {
    const replace = opts && opts.replace;
    // save positions of current level before leaving
    if (state.view) {
      const k = posCacheKey();
      GraphSettings.savePositions(state.tenantId, k.level, k.id, state.view.getPositions());
    }
    state.route = route;
    state.etag = null;
    writeHash(route, replace);
    const nextBucket = settingsBucket();
    if (nextBucket !== state.settingsLevel) loadSettings();
    closePanel(false);
    reload(true);
  }

  async function openPanel(id, node) {
    const panel = document.getElementById("graph-side");
    const body = document.getElementById("graph-side-body");
    const actions = document.getElementById("graph-side-actions");
    if (!panel || !body) return;
    panel.classList.remove("hidden");
    body.textContent = t("loading");
    let card = null;
    try {
      card = await api(`/api/graph/nodes/${encodeURIComponent(id)}`);
    } catch (_) {
      card = null;
    }
    const seed = (card && (card.nodes || []).find((n) => n.id === id)) || (node && node.raw) || node || { id, label: id, type: "work", attrs: {} };
    const attrs = seed.attrs || {};
    const docs = attrs.documents || [];
    const edges = (card && card.edges) || [];
    const secondary = (card && card.secondary) || attrs.secondary || [];
    body.innerHTML = `
      <h3 class="graph-side-title">${esc(seed.label || id)}</h3>
      <dl class="graph-side-meta">
        <div><dt>${t("node")}</dt><dd class="mono">${esc(seed.type || "")} · ${esc(id)}</dd></div>
        <div><dt>${t("author")}</dt><dd>${esc(attrs.author_ru || attrs.author || attrs.author_key || "—")}</dd></div>
        <div><dt>${t("collection")}</dt><dd>${esc(seed.collection || attrs.collection || "—")}</dd></div>
        <div><dt>${t("language")}</dt><dd>${esc(seed.lang || attrs.lang || "—")}</dd></div>
        <div><dt>${t("docKind")}</dt><dd>${esc(seed.doc_kind || attrs.doc_kind || "—")}</dd></div>
        <div><dt>${t("theme")}</dt><dd>${esc(seed.theme_id || attrs.theme_id || "—")}</dd></div>
        <div><dt>${t("topic")}</dt><dd>${esc(seed.topic_id || attrs.topic_id || "—")}</dd></div>
        <div><dt>${t("secondary")}</dt><dd>${esc((secondary || []).map((s) => (s.topic_id || s)).join(", ") || "—")}</dd></div>
      </dl>
      <h4>${t("provenance")}</h4>
      <ul class="graph-side-edges">${edges
        .slice(0, 40)
        .map((e) => {
          const p = e.provenance || {};
          const other = e.source === id ? e.target : e.source;
          return `<li><span class="mono">${esc(e.type)}</span> → ${esc(other)}<br/><span class="muted">${esc(
            [p.phrase, p.context, (p.terms || []).join(", "), e.weight != null ? `вес=${e.weight}` : ""]
              .filter(Boolean)
              .join(" · ") || "—"
          )}</span></li>`;
        })
        .join("") || `<li class="muted">—</li>`}</ul>
    `;
    actions.innerHTML = "";
    actions.style.display = "flex";
    const typ = seed.type || (node && node.type);
    if (typ === "theme") {
      const b = btn(t("theme"), () => navigate({ level: "theme", id }));
      actions.appendChild(b);
    }
    if (typ === "topic") {
      actions.appendChild(btn(t("topic"), () => navigate({ level: "topic", id })));
    }
    actions.appendChild(
      btn(t("showLocal"), () => navigate({ level: "local", id }), "primary")
    );
    actions.appendChild(
      btn(t("unpin"), () => {
        state.view?.pinNode(id, false);
        if (state.view && state.view.kind && state.view.kind() === "sigma") {
          // clear fx/fy via setPositions without pin
          const pos = state.view.getPositions();
          if (pos[id]) {
            state.view.pinNode(id, false);
            state.view.reheat();
          }
        }
        persistPositionsSoon();
      })
    );
    const docId = (docs[0] && docs[0].document_id) || (id.startsWith("work:") ? null : /^\w{8}-/.test(id) ? id : null);
    if (docId || (docs[0] && docs[0].title)) {
      actions.appendChild(
        btn(t("openDoc"), () => {
          document.querySelector('.nav-btn[data-tab="documents"]')?.click();
          const search = document.getElementById("doc-search");
          if (search) {
            search.value = (docs[0] && (docs[0].title || docs[0].filename)) || seed.label || "";
            document.getElementById("btn-doc-search")?.click();
          }
        })
      );
    }
  }

  function closePanel(clearSel) {
    document.getElementById("graph-side")?.classList.add("hidden");
    if (clearSel) state.selectedId = null;
  }

  function btn(label, onClick, cls) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn" + (cls ? ` ${cls}` : "");
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderLocalControls() {
    const box = document.getElementById("graph-local-bar");
    if (!box) return;
    const local = state.route.level === "local";
    box.classList.toggle("hidden", !local);
    if (!local) return;
    const L = state.settings.local || { depth: 2, incoming: true, outgoing: true, undirected: true };
    box.innerHTML = `
      <label>${t("depth")} <input type="range" id="graph-depth-range" min="1" max="3" step="1" value="${L.depth || 2}" /><span id="graph-depth-val">${L.depth || 2}</span></label>
      <label class="chk"><input type="checkbox" id="graph-dir-in" ${L.incoming !== false ? "checked" : ""}/> ${t("incoming")}</label>
      <label class="chk"><input type="checkbox" id="graph-dir-out" ${L.outgoing !== false ? "checked" : ""}/> ${t("outgoing")}</label>
      <label class="chk"><input type="checkbox" id="graph-dir-und" ${L.undirected !== false ? "checked" : ""}/> ${t("neighbors")}</label>
    `;
    const apply = () => {
      state.settings.local = {
        depth: Number(document.getElementById("graph-depth-range").value),
        incoming: document.getElementById("graph-dir-in").checked,
        outgoing: document.getElementById("graph-dir-out").checked,
        undirected: document.getElementById("graph-dir-und").checked,
      };
      document.getElementById("graph-depth-val").textContent = String(state.settings.local.depth);
      saveSettings();
      reload(true);
    };
    box.querySelectorAll("input").forEach((el) => el.addEventListener("change", apply));
  }

  function syncRangeVal(el) {
    if (!el || !el.id) return;
    const badge = document.querySelector(`.gs-val[data-for="${el.id}"]`);
    if (badge) badge.textContent = el.value;
  }

  function toggleRow(id, label, checked) {
    return `<label class="gs-row"><span title="${esc(label)}">${esc(label)}</span><input class="gs-toggle" type="checkbox" id="${id}" ${checked ? "checked" : ""}/></label>`;
  }
  function toggleData(attr, key, label, checked, title) {
    return `<label class="gs-row"><span title="${esc(title || key)}">${esc(label)}</span><input class="gs-toggle" type="checkbox" ${attr}="${esc(key)}" ${checked ? "checked" : ""}/></label>`;
  }
  function rangeRow(id, label, min, max, step, value) {
    return `<div class="gs-row-range"><span>${esc(label)}</span><span class="gs-val" data-for="${id}">${esc(String(value))}</span><input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${value}" /></div>`;
  }

  function buildSettingsPanel() {
    const root = document.getElementById("graph-settings-panel");
    if (!root) return;
    const s = state.settings;
    const open = s.sectionsOpen || {};
    const f = s.filters;
    const edgeKeys = [
      "has_topic",
      "has_work",
      "theme_link",
      "topic_link",
      "topic_knn",
      "work_lex_similar",
      "work_refers_to",
      "work_shared_entity",
      "mentions_author",
      "contains",
      "wrote",
      "part_of",
    ];
    root.innerHTML = `
      <div class="graph-settings-head">
        <strong>${t("settings")}</strong>
        <button type="button" class="gs-reset-icon" id="graph-settings-reset" title="${t("resetSettings")}" aria-label="${t("resetSettings")}">↺</button>
        <button type="button" class="graph-icon-btn" id="graph-settings-close" aria-label="close">×</button>
      </div>
      ${section(
        "filters",
        t("filters"),
        open.filters !== false,
        `
        <input type="search" id="gs-search" placeholder="${t("search")}" value="${esc(f.search || "")}" />
        <p class="muted tiny">${t("searchHint")}</p>
        <p class="gs-subhead">${t("nodeTypes")}</p>
        ${NODE_TYPE_KEYS.map((k) => toggleData("data-nt", k, t("type_" + k), f.nodeTypes[k] !== false)).join("")}
        <p class="gs-subhead">${t("edgeTypes")}</p>
        ${edgeKeys
          .map((k) => {
            const on =
              f.edgeTypes[k] === true ||
              (f.edgeTypes[k] !== false && ["has_topic", "has_work", "theme_link", "topic_link", "topic_knn", "work_lex_similar", "work_refers_to", "work_shared_entity", "wrote"].includes(k));
            return toggleData("data-et", k, t("edge_" + k), on, k);
          })
          .join("")}
        ${toggleRow("gs-only-existing", t("onlyExisting"), !!f.onlyExisting)}
        ${toggleRow("gs-orphans", t("showOrphans"), !!f.orphans)}
        ${toggleRow("gs-unthemed", t("showUnthemed"), f.unthemed !== false)}
        <p class="gs-subhead">${t("language")}</p>
        ${["ru", "en", "mixed", "other"].map((k) => toggleData("data-lang", k, k, f.langs[k] !== false)).join("")}
        <p class="gs-subhead">${t("docKind")}</p>
        ${["fiction", "nonfiction", "technical", "unknown"].map((k) => toggleData("data-kind", k, k, f.kinds[k] !== false)).join("")}
        ${rangeRow("gs-min-weight", t("minWeight"), 0, 1, 0.05, f.minWeight || 0)}
        ${rangeRow("gs-max-edges", t("maxEdgesPerNode"), 1, 20, 1, f.maxEdgesPerNode != null ? f.maxEdgesPerNode : 4)}
      `
      )}
      ${section(
        "groups",
        t("groups"),
        !!open.groups,
        `
        <div id="gs-groups"></div>
        <button type="button" class="btn" id="gs-new-group">${t("newGroup")}</button>
        <button type="button" class="btn ghost" id="gs-color-themes">${t("colorByThemes")}</button>
      `
      )}
      ${section(
        "display",
        t("display"),
        open.display !== false,
        `
        ${toggleRow("gs-arrows", t("arrows"), !!s.display.arrows)}
        ${rangeRow("gs-label-thr", t("textFade"), 0, 1, 0.05, s.display.labelThreshold)}
        ${rangeRow("gs-node-scale", t("nodeSize"), 0.4, 2.5, 0.1, s.display.nodeScale)}
        ${rangeRow("gs-edge-scale", t("edgeWidth"), 0.4, 3, 0.1, s.display.edgeScale)}
        <button type="button" class="btn" id="gs-animate">${t("animate")}</button>
      `
      )}
      ${section(
        "forces",
        t("forces"),
        !!open.forces,
        `
        ${rangeRow("gs-f-center", t("forceCenter"), 0, 1, 0.01, s.forces.center)}
        ${rangeRow("gs-f-charge", t("forceCharge"), -600, -20, 10, s.forces.charge)}
        ${rangeRow("gs-f-link", t("forceLink"), 0, 1, 0.05, s.forces.link)}
        ${rangeRow("gs-f-dist", t("forceDistance"), 20, 200, 5, s.forces.distance)}
      `
      )}
    `;
    root.querySelectorAll(".gs-section-toggle").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-sec");
        state.settings.sectionsOpen[key] = !state.settings.sectionsOpen[key];
        saveSettings();
        buildSettingsPanel();
      });
    });
    document.getElementById("graph-settings-close")?.addEventListener("click", () => {
      root.classList.add("hidden");
    });
    document.getElementById("graph-settings-reset")?.addEventListener("click", () => {
      state.settings = GraphSettings.reset(state.tenantId, state.settingsLevel);
      buildSettingsPanel();
      applyFiltersLive();
    });
    wireSettingsControls();
    renderGroupsEditor();
  }

  function section(key, title, isOpen, inner) {
    return `<div class="gs-section ${isOpen ? "open" : ""}">
      <button type="button" class="gs-section-toggle" data-sec="${key}"><span class="gs-caret">${isOpen ? "▾" : "▸"}</span> ${title}</button>
      <div class="gs-section-body">${isOpen ? inner : ""}</div>
    </div>`;
  }

  function wireSettingsControls() {
    const debounceSearch = () => {
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(runSearch, 200);
    };
    document.getElementById("gs-search")?.addEventListener("input", (e) => {
      state.settings.filters.search = e.target.value;
      saveSettings();
      debounceSearch();
    });
    document.getElementById("gs-search")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const first = state.searchHits.values().next().value;
        if (first) state.view?.focusNode(first);
      }
    });
    document.querySelectorAll("[data-nt]").forEach((el) =>
      el.addEventListener("change", () => {
        state.settings.filters.nodeTypes[el.getAttribute("data-nt")] = el.checked;
        saveSettings();
        applyFiltersLive();
      })
    );
    document.querySelectorAll("[data-et]").forEach((el) =>
      el.addEventListener("change", () => {
        const k = el.getAttribute("data-et");
        state.settings.filters.edgeTypes[k] = el.checked;
        if (k === "has_topic") {
          ["has_work", "theme_link", "topic_link", "topic_knn"].forEach((x) => (state.settings.filters.edgeTypes[x] = el.checked));
        }
        saveSettings();
        applyFiltersLive();
      })
    );
    ["gs-only-existing", "gs-orphans", "gs-unthemed"].forEach((id) => {
      document.getElementById(id)?.addEventListener("change", (e) => {
        if (id === "gs-only-existing") state.settings.filters.onlyExisting = e.target.checked;
        if (id === "gs-orphans") state.settings.filters.orphans = e.target.checked;
        if (id === "gs-unthemed") state.settings.filters.unthemed = e.target.checked;
        saveSettings();
        applyFiltersLive();
      });
    });
    document.querySelectorAll("[data-lang]").forEach((el) =>
      el.addEventListener("change", () => {
        state.settings.filters.langs[el.getAttribute("data-lang")] = el.checked;
        saveSettings();
        applyFiltersLive();
      })
    );
    document.querySelectorAll("[data-kind]").forEach((el) =>
      el.addEventListener("change", () => {
        state.settings.filters.kinds[el.getAttribute("data-kind")] = el.checked;
        saveSettings();
        applyFiltersLive();
      })
    );
    document.getElementById("gs-min-weight")?.addEventListener("input", (e) => {
      state.settings.filters.minWeight = Number(e.target.value);
      saveSettings();
      applyFiltersLive();
      syncRangeVal(e.target);
    });
    document.getElementById("gs-max-edges")?.addEventListener("input", (e) => {
      state.settings.filters.maxEdgesPerNode = Number(e.target.value);
      saveSettings();
      applyFiltersLive();
      syncRangeVal(e.target);
    });
    root.querySelectorAll("input[type='range']").forEach((el) => {
      el.addEventListener("input", () => syncRangeVal(el));
      syncRangeVal(el);
    });
    document.getElementById("gs-arrows")?.addEventListener("change", (e) => {
      state.settings.display.arrows = e.target.checked;
      saveSettings();
      state.view?.setDisplay({ arrows: e.target.checked });
    });
    document.getElementById("gs-label-thr")?.addEventListener("input", (e) => {
      state.settings.display.labelThreshold = Number(e.target.value);
      saveSettings();
      state.view?.setDisplay({ labelThreshold: Number(e.target.value) });
    });
    document.getElementById("gs-node-scale")?.addEventListener("input", (e) => {
      state.settings.display.nodeScale = Number(e.target.value);
      saveSettings();
      applyFiltersLive();
    });
    document.getElementById("gs-edge-scale")?.addEventListener("input", (e) => {
      state.settings.display.edgeScale = Number(e.target.value);
      saveSettings();
      applyFiltersLive();
    });
    document.getElementById("gs-animate")?.addEventListener("click", () => {
      state.settings.display.animate = true;
      if (state.raw) runTimelapse(filterPayload(state.raw, state.settings).nodes);
    });
    const forceMap = {
      "gs-f-center": "center",
      "gs-f-charge": "charge",
      "gs-f-link": "link",
      "gs-f-dist": "distance",
    };
    Object.keys(forceMap).forEach((id) => {
      document.getElementById(id)?.addEventListener("input", (e) => {
        state.settings.forces[forceMap[id]] = Number(e.target.value);
        saveSettings();
        state.view?.setForces(state.settings.forces);
      });
    });
    document.getElementById("gs-new-group")?.addEventListener("click", () => {
      state.settings.groups.push({
        query: "",
        color: GraphSettings.THEME_PALETTE[state.settings.groups.length % GraphSettings.THEME_PALETTE.length],
      });
      saveSettings();
      renderGroupsEditor();
    });
    document.getElementById("gs-color-themes")?.addEventListener("click", () => {
      const themes = (state.raw?.nodes || []).filter((n) => n.type === "theme");
      const ids = [...new Set((state.raw?.nodes || []).map((n) => n.theme_id || (n.attrs && n.attrs.theme_id)).filter(Boolean))];
      const list = themes.length ? themes.map((n) => n.id) : ids;
      state.settings.groups = list.map((id, i) => ({
        query: `theme:${String(id).replace(/^theme:/, "")}`,
        color: GraphSettings.THEME_PALETTE[i % GraphSettings.THEME_PALETTE.length],
      }));
      saveSettings();
      renderGroupsEditor();
      applyFiltersLive();
    });
  }

  function renderGroupsEditor() {
    const box = document.getElementById("gs-groups");
    if (!box) return;
    box.innerHTML = (state.settings.groups || [])
      .map(
        (g, i) => `<div class="gs-group-row">
        <input type="color" data-gi="${i}" data-field="color" value="${esc(g.color || "#7f6df2")}" />
        <input type="text" data-gi="${i}" data-field="query" placeholder="author:Tolstoy" value="${esc(g.query || "")}" />
        <button type="button" class="btn ghost" data-gdel="${i}">×</button>
      </div>`
      )
      .join("");
    box.querySelectorAll("input").forEach((el) => {
      el.addEventListener("change", () => {
        const i = Number(el.getAttribute("data-gi"));
        const field = el.getAttribute("data-field");
        if (!state.settings.groups[i]) return;
        state.settings.groups[i][field] = el.value;
        saveSettings();
        applyFiltersLive();
      });
    });
    box.querySelectorAll("[data-gdel]").forEach((el) => {
      el.addEventListener("click", () => {
        state.settings.groups.splice(Number(el.getAttribute("data-gdel")), 1);
        saveSettings();
        renderGroupsEditor();
        applyFiltersLive();
      });
    });
  }

  async function runSearch() {
    const q = (state.settings.filters.search || "").trim();
    state.searchHits = new Set();
    if (!q) {
      state.view?.highlight({});
      applyFiltersLive();
      return;
    }
    try {
      const data = await api(`/api/graph/search?q=${encodeURIComponent(q)}&limit=200`);
      (data.nodes || []).forEach((n) => state.searchHits.add(n.id));
    } catch (_) {
      // local fallback
      (state.raw?.nodes || []).forEach((n) => {
        if (String(n.label || "").toLowerCase().includes(q.toLowerCase())) state.searchHits.add(n.id);
      });
    }
    if (state.searchHits.size) state.view?.highlight({ focus: null, neighbors: state.searchHits });
    else state.view?.highlight({});
  }

  function applyFiltersLive() {
    if (!state.raw) return;
    const filtered = filterPayload(state.raw, state.settings);
    applyToView(filtered, true);
    document.getElementById("graph-status-line").textContent = `${filtered.nodes.length} узлов · ${filtered.edges.length} связей`;
  }

  function syncSettingsForm() {
    const panel = document.getElementById("graph-settings-panel");
    if (panel && !panel.classList.contains("hidden")) buildSettingsPanel();
  }

  async function rebuild() {
    setBanner("Перестройка…", true);
    try {
      await api("/api/graph/rebuild?scope=all", { method: "POST" });
      GraphSettings.clearPositions(state.tenantId);
      state.etag = null;
      window.toast?.("Перестройка запущена", "ok");
      setTimeout(() => reload(true), 1500);
    } catch (e) {
      window.toast?.(String(e.message || e), "bad");
    }
  }

  function wire() {
    if (state.wired) return;
    state.wired = true;
    loadSettings();
    const parsed = parseHash();
    if (parsed) state.route = parsed;
    else writeHash(state.route, true);

    document.getElementById("btn-graph-settings")?.addEventListener("click", () => {
      const panel = document.getElementById("graph-settings-panel");
      panel.classList.toggle("hidden");
      if (!panel.classList.contains("hidden")) buildSettingsPanel();
    });
    document.getElementById("btn-graph-rebuild")?.addEventListener("click", () => rebuild());
    document.getElementById("btn-graph-theme")?.addEventListener("click", () => {
      const root = document.documentElement;
      const next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
      if (next === "light") root.setAttribute("data-theme", "light");
      else root.removeAttribute("data-theme");
      try {
        localStorage.setItem("rag5060.ui.theme", next);
      } catch (_) {}
      // Update sigma settings in place (no layout reset / no page reload).
      if (state.view) {
        const colors = themeColors();
        state.view.setDisplay({
          accent: colors.accent,
          nodeColor: colors.nodeColor,
          edgeColor: colors.edgeColor,
          labelColor: colors.labelColor,
          bg: colors.bg,
        });
      }
    });
    document.getElementById("btn-graph-fit")?.addEventListener("click", () => state.view?.fit());
    document.getElementById("btn-graph-zoom-in")?.addEventListener("click", () => state.view?.zoomBy(0.8));
    document.getElementById("btn-graph-zoom-out")?.addEventListener("click", () => state.view?.zoomBy(1.25));
    document.getElementById("btn-graph-back")?.addEventListener("click", () => history.back());
    document.getElementById("graph-side-close")?.addEventListener("click", () => closePanel(true));
    try {
      if (localStorage.getItem("rag5060.ui.theme") === "light") {
        document.documentElement.setAttribute("data-theme", "light");
      }
    } catch (_) {}

    window.addEventListener("hashchange", () => {
      if (!state.visible) return;
      const next = parseHash();
      if (!next) return;
      if (next.level === state.route.level && next.id === state.route.id) return;
      if (state.view) {
        const k = posCacheKey();
        GraphSettings.savePositions(state.tenantId, k.level, k.id, state.view.getPositions());
      }
      state.route = next;
      state.etag = null;
      loadSettings();
      closePanel(false);
      reload(true);
    });

    document.addEventListener("keydown", (e) => {
      if (!state.visible) return;
      if (e.key === "Backspace" && !/INPUT|TEXTAREA|SELECT/.test((e.target && e.target.tagName) || "")) {
        e.preventDefault();
        history.back();
      }
    });
  }

  function onShow() {
    state.visible = true;
    wire();
    const parsed = parseHash();
    if (parsed) state.route = parsed;
    else writeHash(state.route, true);
    ensureView();
    reload(false);
    setTimeout(() => state.view?.resize(), 50);
  }

  function onHide() {
    state.visible = false;
    if (state.view) {
      const k = posCacheKey();
      GraphSettings.savePositions(state.tenantId, k.level, k.id, state.view.getPositions());
    }
  }

  global.GraphUI = { wire, onShow, onHide, navigate, reload };
})(typeof window !== "undefined" ? window : globalThis);
