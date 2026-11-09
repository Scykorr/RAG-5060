/* Graph Explorer — Cytoscape.js (local vendor), no CDN */
/* global cytoscape */

const GraphUI = (() => {
  let cy = null;
  let loadSeq = 0;
  let selectedEdgeId = null;

  const EDGE_STYLE = {
    SEMANTIC_SIMILAR: { color: "#5b8cff", lineStyle: "solid", label: "похож" },
    EXPLICIT_REFERENCE: { color: "#3ecfb2", lineStyle: "solid", label: "ссылается" },
    SAME_FAMILY: { color: "#f0c674", lineStyle: "dashed", label: "семейство" },
    MANUAL_LINK: { color: "#c9a0ff", lineStyle: "solid", label: "вручную" },
    POTENTIAL_CONTRADICTION: { color: "#ff6b7a", lineStyle: "dashed", label: "спорно?" },
  };

  function destroy() {
    if (cy) {
      try {
        cy.destroy();
      } catch (_) {
        /* ignore */
      }
      cy = null;
    }
  }

  function ensureCy() {
    const el = document.getElementById("graph-cy");
    if (!el || typeof cytoscape !== "function") return null;
    if (cy) {
      cy.resize();
      return cy;
    }
    cy = cytoscape({
      container: el,
      elements: [],
      style: [
        {
          selector: "node",
          style: {
            label: "data(labelShort)",
            color: "#e8eef8",
            "background-color": "#1a2438",
            "border-width": 2,
            "border-color": "#3ecfb2",
            "font-size": 10,
            "text-wrap": "ellipsis",
            "text-max-width": 90,
            "text-valign": "bottom",
            "text-margin-y": 6,
            width: 28,
            height: 28,
            shape: "round-rectangle",
          },
        },
        {
          selector: "node:selected",
          style: { "border-color": "#5b8cff", "border-width": 3, "background-color": "#243352" },
        },
        {
          selector: "node.center",
          style: { "background-color": "#2bb89e", "border-color": "#e8eef8", width: 34, height: 34 },
        },
        {
          selector: "edge",
          style: {
            width: 2,
            "line-color": "data(color)",
            "target-arrow-color": "data(color)",
            "curve-style": "bezier",
            "line-style": "data(lineStyle)",
            opacity: 0.85,
          },
        },
        {
          selector: "edge.directed",
          style: { "target-arrow-shape": "triangle" },
        },
        {
          selector: "edge:selected",
          style: { width: 4, opacity: 1 },
        },
      ],
      layout: { name: "cose", animate: false, padding: 30 },
      wheelSensitivity: 0.25,
    });
    cy.on("tap", "node", (evt) => {
      const d = evt.target.data();
      showNodeDetails(d);
      loadRelationsTable(d.document_id);
    });
    cy.on("tap", "edge", (evt) => {
      const d = evt.target.data();
      selectedEdgeId = d.edge_id;
      showEdgeDetails(d);
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        selectedEdgeId = null;
        setDetails("Выберите узел или связь.");
        document.getElementById("graph-details-actions").style.display = "none";
      }
    });
    return cy;
  }

  function shortLabel(s) {
    const t = String(s || "");
    return t.length > 28 ? `${t.slice(0, 26)}…` : t;
  }

  function selectedTypes() {
    return [...document.querySelectorAll(".graph-type:checked")].map((el) => el.value);
  }

  function setBanner(text, show) {
    const el = document.getElementById("graph-banner");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("hidden", !show);
  }

  function setEmpty(show) {
    document.getElementById("graph-empty")?.classList.toggle("hidden", !show);
  }

  function setDetails(htmlOrText, asHtml = false) {
    const body = document.getElementById("graph-details-body");
    if (!body) return;
    if (asHtml) body.innerHTML = htmlOrText;
    else body.textContent = htmlOrText;
  }

  function esc(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function refreshStatus() {
    try {
      const st = await window.api("/api/graph/status");
      const line = document.getElementById("graph-status-line");
      if (line) {
        line.textContent = `Документов: ${st.documents_visible}; рёбер: ${st.edges_stored}; векторный поиск: ${st.vector_search}; метод: ${st.similarity_method}`;
      }
      if (st.vector_search === "DEGRADED_NO_VECTOR_SEARCH") {
        setBanner(
          "Векторные embeddings пока заглушка — сходство строится лексически (token Jaccard). Не путать со смысловой «истинностью».",
          true
        );
      }
      return st;
    } catch (e) {
      return null;
    }
  }

  function toElements(nodes, edges, centerId) {
    const n = (nodes || []).map((node) => ({
      data: {
        id: node.id,
        label: node.label,
        labelShort: shortLabel(node.label),
        document_id: node.document_id,
        status: node.status,
        relation_count_visible: node.relation_count_visible,
      },
      classes: centerId && node.document_id === centerId ? "center" : "",
    }));
    const e = (edges || []).map((edge) => {
      const meta = EDGE_STYLE[edge.relation_type] || { color: "#8fa0b8", lineStyle: "solid" };
      return {
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          edge_id: edge.edge_id,
          relation_type: edge.relation_type,
          origin: edge.origin,
          review_state: edge.review_state,
          score: edge.score,
          score_method: edge.score_method,
          evidence_count: edge.evidence_count,
          payload_version: edge.payload_version,
          color: meta.color,
          lineStyle: meta.lineStyle,
        },
        classes: edge.directed ? "directed" : "",
      };
    });
    return [...n, ...e];
  }

  async function load() {
    const seq = ++loadSeq;
    const mode = document.getElementById("graph-mode")?.value || "global";
    const types = selectedTypes().join(",");
    const minScore = Number(document.getElementById("graph-min-score")?.value || 0);
    const prefix = document.getElementById("graph-prefix")?.value || "";
    const search = document.getElementById("graph-search")?.value || "";
    const depth = Number(document.getElementById("graph-depth")?.value || 1);
    let center = (document.getElementById("graph-center")?.value || "").trim();

    await refreshStatus();
    const inst = ensureCy();
    if (!inst) {
      setDetails("Cytoscape не загружен (проверьте /vendor/cytoscape.min.js).");
      return;
    }

    let payload;
    if (mode === "local") {
      if (!center) {
        // pick first searchable doc
        const nodes = await window.api(
          `/api/graph/nodes?limit=1&search=${encodeURIComponent(search)}&prefix=${encodeURIComponent(prefix)}`
        );
        if (seq !== loadSeq) return;
        center = nodes.nodes?.[0]?.document_id;
        if (center) document.getElementById("graph-center").value = center;
      }
      if (!center) {
        setEmpty(true);
        setDetails("Нет доступных документов для локального графа.");
        return;
      }
      const qs = new URLSearchParams({
        depth: String(depth),
        max_nodes: "200",
        max_edges: "400",
        relation_types: types,
        min_score: String(minScore),
      });
      payload = await window.api(`/api/graph/neighborhood/${center}?${qs}`);
      if (seq !== loadSeq) return;
    } else {
      const nodes = await window.api(
        `/api/graph/nodes?limit=200&search=${encodeURIComponent(search)}&prefix=${encodeURIComponent(prefix)}`
      );
      if (seq !== loadSeq) return;
      const ids = (nodes.nodes || []).map((n) => n.document_id).join(",");
      const edges = ids
        ? await window.api(
            `/api/graph/edges?node_ids=${encodeURIComponent(ids)}&relation_types=${encodeURIComponent(types)}&min_score=${minScore}&limit=400`
          )
        : { edges: [], truncated: false };
      if (seq !== loadSeq) return;
      payload = {
        nodes: nodes.nodes,
        edges: edges.edges,
        truncated: !!(nodes.truncated || edges.truncated),
        center_document_id: null,
      };
    }

    const elements = toElements(payload.nodes, payload.edges, payload.center_document_id);
    inst.elements().remove();
    inst.add(elements);
    const layoutName = mode === "local" ? "breadthfirst" : "cose";
    inst
      .layout({
        name: layoutName,
        animate: false,
        fit: true,
        padding: 28,
        directed: true,
        spacingFactor: 1.15,
      })
      .run();
    setEmpty(elements.length === 0);
    const nCount = (payload.nodes || []).length;
    const eCount = (payload.edges || []).length;
    if (payload.truncated) {
      setBanner(`Частичный граф: показано ${nCount} узлов / ${eCount} связей. Есть ещё данные за лимитом.`, true);
    } else if (nCount && !eCount) {
      setBanner(
        `Узлов: ${nCount}, связей пока нет. Нажмите «Анализ корпуса» (admin), чтобы построить рёбра.`,
        true
      );
    } else if (!document.getElementById("graph-banner")?.textContent.includes("Векторные")) {
      setBanner(`Показано ${nCount} узлов / ${eCount} связей.`, !!nCount);
    }
    syncUrl({ mode, center, depth, types });
  }

  function syncUrl({ mode, center, depth, types }) {
    try {
      const u = new URL(window.location.href);
      u.searchParams.set("tab", "graph");
      if (mode) u.searchParams.set("gmode", mode);
      if (center) u.searchParams.set("doc", center);
      if (depth) u.searchParams.set("depth", String(depth));
      if (types) u.searchParams.set("types", types);
      history.replaceState(null, "", u);
    } catch (_) {
      /* ignore */
    }
  }

  function showNodeDetails(d) {
    setDetails(
      `Документ: ${d.label || ""}\nID: ${d.document_id}\nСтатус: ${d.status}\nСвязей видно: ${d.relation_count_visible ?? "—"}`
    );
    const actions = document.getElementById("graph-details-actions");
    actions.style.display = "flex";
    actions.innerHTML = "";
    const open = document.createElement("button");
    open.className = "btn primary";
    open.type = "button";
    open.textContent = "Открыть в Документах";
    open.addEventListener("click", () => {
      document.querySelector('.nav-btn[data-tab="documents"]')?.click();
      const search = document.getElementById("doc-search");
      if (search) {
        search.value = d.label || "";
        document.getElementById("btn-doc-search")?.click();
      }
    });
    const asCenter = document.createElement("button");
    asCenter.className = "btn";
    asCenter.type = "button";
    asCenter.textContent = "Локальный граф";
    asCenter.addEventListener("click", () => {
      document.getElementById("graph-mode").value = "local";
      document.getElementById("graph-center").value = d.document_id;
      load().catch((e) => window.toast?.(e.message, "bad"));
    });
    actions.append(open, asCenter);
  }

  async function showEdgeDetails(d) {
    setDetails("Загрузка доказательств…");
    const actions = document.getElementById("graph-details-actions");
    actions.style.display = "flex";
    actions.innerHTML = "";
    try {
      const edge = await window.api(`/api/graph/edges/${d.edge_id}`);
      const ev = await window.api(`/api/graph/edges/${d.edge_id}/evidence`);
      const items = (ev.items || [])
        .map((it, i) => {
          const snip = [it.source_snippet, it.target_snippet].filter(Boolean).map(esc).join("\n---\n");
          return `<div class="card" style="margin:0.4rem 0;padding:0.6rem"><div class="muted">#${i + 1} ${esc(it.method)} p.${esc(it.page ?? "—")}</div><div>${esc(it.explanation || "")}</div><pre class="sys">${snip || "фрагмент недоступен / агрегатный метод"}</pre></div>`;
        })
        .join("");
      setDetails(
        `<div><strong>${esc(edge.relation_type)}</strong> · ${esc(edge.origin)} · ${esc(edge.review_state)}</div>
         <div class="muted">score=${esc(edge.score ?? "—")} (${esc(edge.score_method || "n/a")})</div>
         <div class="muted" style="margin:0.35rem 0">Похожесть ≠ истинность утверждений.</div>
         <div class="muted">A: ${esc(edge.source_document_id)} → B: ${esc(edge.target_document_id)}</div>
         ${items || "<p class='muted'>Нет evidence rows</p>"}`,
        true
      );
      if (window.state?.user?.role === "admin") {
        const mk = (label, action) => {
          const b = document.createElement("button");
          b.className = "btn";
          b.type = "button";
          b.textContent = label;
          b.addEventListener("click", async () => {
            try {
              await window.api(`/api/graph/edges/${d.edge_id}/review`, {
                method: "POST",
                body: { action, expected_version: edge.payload_version },
              });
              window.toast?.(`Связь: ${action}`, "ok");
              await load();
            } catch (err) {
              window.toast?.(err.message, "bad");
            }
          });
          return b;
        };
        actions.append(mk("Подтвердить", "APPROVE"), mk("Отклонить", "REJECT"));
      }
    } catch (err) {
      setDetails(String(err.message || err));
    }
  }

  async function loadRelationsTable(documentId) {
    const box = document.getElementById("graph-relations-table");
    if (!box || !documentId) return;
    try {
      const data = await window.api(`/api/graph/documents/${documentId}/relations?limit=30`);
      if (!data.items?.length) {
        box.textContent = "Связей нет";
        return;
      }
      box.innerHTML = `<table><thead><tr><th>Тип</th><th>Сосед</th><th>Score</th><th>Статус</th></tr></thead><tbody>${data.items
        .map((e) => {
          const other =
            e.source_document_id === documentId ? e.target_document_id : e.source_document_id;
          return `<tr><td>${esc(e.relation_type)}</td><td class="mono">${esc(other.slice(0, 8))}…</td><td>${esc(e.score ?? "—")}</td><td>${esc(e.review_state)}</td></tr>`;
        })
        .join("")}</tbody></table>`;
    } catch (err) {
      box.textContent = err.message || String(err);
    }
  }

  async function rebuild(mode) {
    const body = { mode };
    if (mode === "rebuild_doc_graph" || mode === "analyze_new_version") {
      const id = (document.getElementById("graph-center")?.value || "").trim();
      if (!id) {
        window.toast?.("Укажите document id центра", "bad");
        return;
      }
      body.document_id = id;
      body.mode = "rebuild_doc_graph";
    }
    const dry = await window.api("/api/graph/jobs", { method: "POST", body: { ...body, dry_run: true } });
    if (
      !confirm(
        `Запустить анализ графа?\nmode=${body.mode}\nдокументов≈${dry.documents}\nЭто фоновая задача с лимитом нагрузки.`
      )
    ) {
      return;
    }
    const job = await window.api("/api/graph/jobs", { method: "POST", body });
    window.toast?.(`Graph job ${job.state}: ${job.id?.slice(0, 8)}…`, job.state === "SUCCEEDED" ? "ok" : "info");
    await load();
  }

  function applyQueryParams() {
    const u = new URL(window.location.href);
    if (u.searchParams.get("tab") === "graph" || u.hash === "#graph") {
      /* tab switch handled by app */
    }
    const mode = u.searchParams.get("gmode");
    const doc = u.searchParams.get("doc");
    const depth = u.searchParams.get("depth");
    if (mode) document.getElementById("graph-mode").value = mode;
    if (doc) document.getElementById("graph-center").value = doc;
    if (depth) document.getElementById("graph-depth").value = depth;
  }

  function wire() {
    document.getElementById("btn-graph-load")?.addEventListener("click", () =>
      load().catch((e) => window.toast?.(e.message, "bad"))
    );
    document.getElementById("btn-graph-fit")?.addEventListener("click", () => cy?.fit(undefined, 30));
    document.getElementById("btn-graph-reset")?.addEventListener("click", () => {
      document.getElementById("graph-search").value = "";
      document.getElementById("graph-prefix").value = "";
      document.getElementById("graph-center").value = "";
      document.getElementById("graph-mode").value = "global";
      load().catch(() => {});
    });
    document.getElementById("btn-graph-rebuild")?.addEventListener("click", () =>
      rebuild("rebuild_collection_graph").catch((e) => window.toast?.(e.message, "bad"))
    );
    document.getElementById("btn-graph-rebuild-doc")?.addEventListener("click", () =>
      rebuild("rebuild_doc_graph").catch((e) => window.toast?.(e.message, "bad"))
    );
    window.addEventListener("resize", () => cy?.resize());
  }

  return {
    onShow() {
      applyQueryParams();
      ensureCy();
      refreshStatus().then(() => load().catch(() => {}));
    },
    onHide() {
      destroy();
    },
    wire,
    load,
  };
})();

window.GraphUI = GraphUI;
document.addEventListener("DOMContentLoaded", () => GraphUI.wire());
