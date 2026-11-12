/** Persist Obsidian-like graph settings in localStorage (per tenant + level). */
(function (global) {
  "use strict";

  const DEFAULTS = {
    filters: {
      search: "",
      nodeTypes: {
        theme: true,
        topic: true,
        work: true,
        document: false,
        author: true,
        collection: false,
      },
      edgeTypes: {
        has_topic: true,
        has_work: true,
        theme_link: true,
        topic_link: true,
        topic_knn: true,
        work_lex_similar: true,
        work_refers_to: true,
        refers_to: true,
        work_shared_entity: true,
        shared_entity: true,
        mentions_author: false,
        contains: false,
        wrote: true,
        part_of: false,
        in_theme: false,
        in_collection: false,
      },
      onlyExisting: true,
      orphans: false,
      unthemed: true,
      langs: { ru: true, en: true, mixed: true, other: true, unknown: true },
      kinds: { fiction: true, nonfiction: true, technical: true, unknown: true },
      minWeight: 0,
    },
    groups: [],
    display: {
      arrows: true,
      labelThreshold: 0.55,
      nodeScale: 1,
      edgeScale: 1,
      animate: false,
    },
    forces: { center: 0.05, charge: -180, link: 0.4, distance: 80 },
    sectionsOpen: { filters: true, groups: false, display: true, forces: false },
    local: { depth: 2, incoming: true, outgoing: true, undirected: true },
  };

  function key(tenantId, level) {
    return `rag5060.graph.settings.v1.${tenantId || "default"}.${level || "global"}`;
  }

  function posKey(tenantId, level, id) {
    return `rag5060.graph.pos.v1.${tenantId || "default"}.${level || "overview"}.${id || "root"}`;
  }

  function deepMerge(base, over) {
    if (!over || typeof over !== "object") return JSON.parse(JSON.stringify(base));
    const out = Array.isArray(base) ? base.slice() : { ...base };
    Object.keys(over).forEach((k) => {
      if (over[k] && typeof over[k] === "object" && !Array.isArray(over[k])) {
        out[k] = deepMerge(base[k] || {}, over[k]);
      } else {
        out[k] = over[k];
      }
    });
    return out;
  }

  function load(tenantId, level) {
    try {
      const raw = localStorage.getItem(key(tenantId, level));
      if (!raw) return deepMerge(DEFAULTS, {});
      return deepMerge(DEFAULTS, JSON.parse(raw));
    } catch (_) {
      return deepMerge(DEFAULTS, {});
    }
  }

  function save(tenantId, level, settings) {
    try {
      localStorage.setItem(key(tenantId, level), JSON.stringify(settings));
    } catch (_) {}
  }

  function reset(tenantId, level) {
    try {
      localStorage.removeItem(key(tenantId, level));
    } catch (_) {}
    return deepMerge(DEFAULTS, {});
  }

  function loadPositions(tenantId, level, id) {
    try {
      const raw = localStorage.getItem(posKey(tenantId, level, id));
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function savePositions(tenantId, level, id, pos) {
    try {
      localStorage.setItem(posKey(tenantId, level, id), JSON.stringify(pos || {}));
    } catch (_) {}
  }

  function clearPositions(tenantId) {
    const prefix = `rag5060.graph.pos.v1.${tenantId || "default"}.`;
    try {
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith(prefix)) keys.push(k);
      }
      keys.forEach((k) => localStorage.removeItem(k));
    } catch (_) {}
  }

  const THEME_PALETTE = [
    "#7f6df2",
    "#e67e22",
    "#2ecc71",
    "#3498db",
    "#e74c3c",
    "#1abc9c",
    "#9b59b6",
    "#f1c40f",
    "#16a085",
    "#c0392b",
  ];

  global.GraphSettings = {
    DEFAULTS,
    THEME_PALETTE,
    load,
    save,
    reset,
    loadPositions,
    savePositions,
    clearPositions,
  };
})(typeof window !== "undefined" ? window : globalThis);
