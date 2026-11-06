/* Local SPA — no CDN, no remote fonts/assets */
const state = {
  user: null,
  tab: "chat",
  chats: [],
  activeChatId: null,
  messages: [],
  dispatcher: null,
  system: null,
  logs: null,
  storage: null,
  models: null,
  docs: [],
  docsMeta: null,
  quality: null,
  qualityRuns: [],
  dispatcherTimer: null,
  activeModelJobId: null,
  qualityPollTimer: null,
};

const TITLES = {
  chat: "Чат",
  dispatcher: "Dashboard",
  system: "System",
  documents: "Документы",
  storage: "Хранилище",
  quality: "Качество",
  models: "Модели",
  settings: "Настройки",
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: {
      ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...opts.headers,
    },
    ...opts,
    body:
      opts.body && !(opts.body instanceof FormData) && typeof opts.body !== "string"
        ? JSON.stringify(opts.body)
        : opts.body,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const err = new Error((data && (data.detail || data.message)) || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`;
  return `${(n / 1073741824).toFixed(2)} GB`;
}

function showLogin(show) {
  $("#login-gate").classList.toggle("hidden", !show);
  $("#app-shell").classList.toggle("hidden", show);
  if (show && state.dispatcherTimer) {
    clearInterval(state.dispatcherTimer);
    state.dispatcherTimer = null;
  }
}

function setTab(name) {
  state.tab = name;
  $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("hidden", p.id !== `panel-${name}`));
  $("#page-title").textContent = TITLES[name] || name;
  if (name === "dispatcher" || name === "system" || name === "quality") startDispatcherPoll();
  else if (state.dispatcherTimer) {
    clearInterval(state.dispatcherTimer);
    state.dispatcherTimer = null;
  }
  refreshTab();
}

function startDispatcherPoll() {
  if (state.dispatcherTimer) clearInterval(state.dispatcherTimer);
  const tick = () => {
    if (state.tab === "dispatcher") loadDispatcher();
    if (state.tab === "system") loadSystem();
    if (state.tab === "quality") loadQuality();
  };
  tick();
  state.dispatcherTimer = setInterval(tick, 5000);
}

async function refreshTab() {
  try {
    if (state.tab === "chat") {
      await loadChats();
      if (state.activeChatId) await openChat(state.activeChatId, false);
      else renderChatLog();
    }
    if (state.tab === "dispatcher") await loadDispatcher();
    if (state.tab === "system") await loadSystem();
    if (state.tab === "storage") {
      state.storage = await api("/api/admin/storage");
      renderStorage();
    }
    if (state.tab === "quality") await loadQuality();
    if (state.tab === "models") {
      state.models = await api("/api/admin/models");
      renderModels();
      if (state.activeModelJobId) pollModelJob(state.activeModelJobId).catch(() => {});
    }
    if (state.tab === "documents") {
      await loadDocs();
      refreshInbox().catch(() => {});
    }
  } catch (e) {
    if (e.status === 401) showLogin(true);
    else toast(String(e.message || e), "bad");
  }
}

async function loadChats() {
  const data = await api("/api/chats");
  state.chats = data.items || [];
  renderChatSessions();
}

function renderChatSessions() {
  const list = $("#chat-session-list");
  if (!state.chats.length) {
    list.innerHTML = `<p class="muted">Пока нет чатов. Напишите вопрос — создастся новый.</p>`;
    return;
  }
  list.innerHTML = state.chats
    .map((c) => {
      const active = c.id === state.activeChatId ? "active" : "";
      const when = (c.updated_at || c.created_at || "").replace("T", " ").slice(0, 16);
      return `<button type="button" class="session-item ${active}" data-sid="${esc(c.id)}">
        <div class="row"><div class="t">${esc(c.title || "Чат")}</div>
        <span class="session-del" data-del="${esc(c.id)}" title="Удалить">×</span></div>
        <div class="m">${esc(when)}</div>
      </button>`;
    })
    .join("");
  $$("[data-sid]").forEach((b) =>
    b.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-del]")) return;
      openChat(b.dataset.sid);
    })
  );
  $$("[data-del]").forEach((b) =>
    b.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      try {
        await api(`/api/chats/${b.dataset.del}`, { method: "DELETE" });
        if (state.activeChatId === b.dataset.del) {
          state.activeChatId = null;
          state.messages = [];
        }
        await loadChats();
        renderChatLog();
        toast("Чат удалён", "ok");
      } catch (e) {
        toast(e.message, "bad");
      }
    })
  );
}

async function openChat(id, fetchMessages = true) {
  state.activeChatId = id;
  if (fetchMessages) {
    const data = await api(`/api/chats/${id}`);
    state.messages = data.messages || [];
  }
  renderChatSessions();
  renderChatLog();
}

function renderChatLog() {
  const log = $("#chat-log");
  if (!state.messages.length) {
    log.innerHTML = `<p class="muted">Задайте вопрос по загруженным документам. История сохраняется локально.</p>`;
    return;
  }
  log.innerHTML = state.messages
    .map((m) => {
      const cls = m.role === "user" ? "user" : "bot";
      const cites = (m.citations || [])
        .map((c) => `<li class="mono">${esc(c.document_id)} · ${esc(c.chunk_id)} · p.${esc(c.page ?? "—")}</li>`)
        .join("");
      const pill = m.outcome ? `<div class="pill">${esc(m.outcome)}${m.model ? " · " + esc(m.model) : ""}</div>` : "";
      return `<div class="bubble ${cls}">${pill}<div>${esc(m.content || "")}</div>${cites ? `<ul>${cites}</ul>` : ""}</div>`;
    })
    .join("");
  log.scrollTop = log.scrollHeight;
}

async function loadDispatcher() {
  try {
    const d = await api("/api/admin/dispatcher");
    state.dispatcher = d;
    renderDispatcher();
  } catch (e) {
    if (e.status === 401) showLogin(true);
    else toast(e.message, "bad");
  }
}

async function loadSystem() {
  try {
    const [sys, logs] = await Promise.all([
      api("/api/admin/system"),
      api("/api/admin/logs?limit=40"),
    ]);
    state.system = sys;
    state.logs = logs;
    renderSystem();
  } catch (e) {
    if (e.status === 401) showLogin(true);
    else toast(e.message, "bad");
  }
}

function renderSystem() {
  const s = state.system || {};
  const disk = s.disk || {};
  const q = s.queue || {};
  $("#system-metrics").innerHTML = `
    <div class="card"><div class="label">Offline</div><div class="value">${s.offline !== false ? "yes" : "no"}</div></div>
    <div class="card"><div class="label">Compose profile</div><div class="value">${esc(s.compose_profile || "core")}</div></div>
    <div class="card"><div class="label">Services</div><div class="value">${s.services_ok ? "OK" : "errors"}</div></div>
    <div class="card"><div class="label">Active model</div><div class="value">${esc(s.active_model || "—")}</div></div>
    <div class="card"><div class="label">Ingestion queue</div><div class="value">${q.ingestion ?? 0}</div></div>
    <div class="card"><div class="label">LLM slot</div><div class="value">${s.llm_slot ?? 1}</div></div>
    <div class="card"><div class="label">Disk free</div><div class="value">${disk.free_gb ?? "—"} GB</div></div>
    <div class="card"><div class="label">GPU / VRAM</div><div class="value">${esc((s.gpu && (s.gpu.loaded || s.gpu.note)) || "—")}</div></div>`;
  $("#system-hints").textContent = JSON.stringify(
    { hints: s.launcher_hints || {}, disk, gpu: s.gpu || {}, data_root: disk.data_root },
    null,
    2
  );
  $("#system-deps").textContent = JSON.stringify(s.dependency_errors || {}, null, 2);
  const items = (state.logs && state.logs.items) || [];
  $("#system-logs").textContent = items.length
    ? items.map((x) => `${(x.ts || "").slice(0, 19)} [${x.source}/${x.level}] ${x.message}`).join("\n")
    : "(нет недавних ошибок jobs)";
}

async function pollModelJob(jobId) {
  state.activeModelJobId = jobId;
  const el = $("#model-job-status");
  for (let i = 0; i < 600; i++) {
    const j = await api(`/api/admin/models/jobs/${jobId}`);
    if (el) {
      el.innerHTML = `Job <span class="mono">${esc(jobId.slice(0, 8))}</span>: ${esc(j.status)} ${esc(j.progress)}% ${esc(j.detail || "")}
        ${j.status === "running" ? ` <button type="button" class="btn ghost" id="btn-cancel-job">Cancel</button>` : ""}`;
      const btn = $("#btn-cancel-job");
      if (btn) {
        btn.onclick = async () => {
          try {
            await api(`/api/admin/models/jobs/${jobId}/cancel`, { method: "POST" });
            toast("Cancel requested", "ok");
          } catch (e) {
            toast(e.message, "bad");
          }
        };
      }
    }
    if (j.status === "done" || j.status === "failed" || j.status === "cancelled") {
      state.activeModelJobId = null;
      if (j.status === "done") toast("Model job done", "ok");
      if (j.status === "failed") toast(j.detail || "Model job failed", "bad");
      refreshTab();
      return j;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
}

function renderDispatcher() {
  const d = state.dispatcher || {};
  $("#dispatcher-stamp").textContent = `Обновлено: ${(d.generated_at || "—").replace("T", " ").slice(0, 19)} UTC · UI offline=${d.offline !== false}`;
  const q = d.queues || {};
  const c = d.corpus || {};
  const disk = d.disk || {};
  $("#dispatcher-metrics").innerHTML = `
    <div class="card"><div class="label">Сервисы</div><div class="value">${d.services_ok ? "все OK" : "есть сбои"}</div></div>
    <div class="card"><div class="label">Активная LLM</div><div class="value">${esc(d.active_model || "—")}</div></div>
    <div class="card"><div class="label">Ingestion active</div><div class="value">${q.ingestion_active ?? 0}</div></div>
    <div class="card"><div class="label">Диск free</div><div class="value">${disk.free_gb ?? "—"} GB</div></div>
    <div class="card"><div class="label">Документы</div><div class="value">${c.documents_active ?? 0}</div></div>
    <div class="card"><div class="label">Чанки</div><div class="value">${c.chunks ?? 0}</div></div>
    <div class="card"><div class="label">Чаты</div><div class="value">${c.chat_sessions ?? 0}</div></div>
    <div class="card"><div class="label">LLM слот</div><div class="value">${q.llm_slot ?? 1}</div></div>`;

  const services = d.services || {};
  $("#dispatcher-services").innerHTML = Object.entries(services)
    .map(([k, v]) => {
      const ok = v && v.ok;
      return `<div class="card svc ${ok ? "ok" : "bad"}"><div class="svc-name">${esc(k)}</div><div class="svc-st">${ok ? "healthy" : esc((v && (v.error || v.status_code)) || "down")}</div></div>`;
    })
    .join("");

  $("#dispatcher-queues").textContent = JSON.stringify(
    {
      ...q,
      ollama_loaded: d.ollama_loaded || [],
      storage: d.storage || {},
    },
    null,
    2
  );
  $("#dispatcher-corpus").textContent = JSON.stringify({ ...(c || {}), inbox: d.inbox || {} }, null, 2);

  const jobs = d.recent_ingestion_jobs || [];
  $("#dispatcher-jobs").innerHTML = jobs.length
    ? `<table><thead><tr><th>Stage</th><th>Progress</th><th>Attempts</th><th>Doc</th><th>Error</th></tr></thead><tbody>${jobs
        .map(
          (j) => `<tr>
      <td><span class="pill">${esc(j.stage)}</span></td>
      <td>${esc(j.progress)}</td>
      <td>${esc(j.attempts)}</td>
      <td class="mono">${esc(String(j.document_id || "").slice(0, 8))}</td>
      <td class="muted">${esc(j.safe_error || "—")}</td>
    </tr>`
        )
        .join("")}</tbody></table>`
    : `<p class="muted">Нет ingestion jobs</p>`;

  const mj = d.recent_model_jobs || [];
  $("#dispatcher-model-jobs").innerHTML = mj.length
    ? mj
        .map((j) => `<div class="muted" style="margin-bottom:0.5rem"><strong>${esc(j.name)}</strong> · ${esc(j.source)} · ${esc(j.status)} · ${esc(j.progress)}%</div>`)
        .join("")
    : `<p class="muted">Нет model jobs</p>`;
  $("#dispatcher-folder").textContent = JSON.stringify(d.folder_import || {}, null, 2);
}

function renderStorage() {
  const st = state.storage || {};
  const c = st.corpus || {};
  $("#storage-summary").innerHTML = `
    <div class="card"><div class="label">Документы</div><div class="value">${c.documents_active ?? "—"}</div></div>
    <div class="card"><div class="label">Чанки</div><div class="value">${c.chunks ?? "—"}</div></div>
    <div class="card"><div class="label">Data root</div><div class="value mono" style="font-size:0.95rem">${esc(st.data_root || "—")}</div></div>
    <div class="card"><div class="label">Originals</div><div class="value">${fmtBytes(st.originals_bytes || 0)}</div></div>
    <div class="card"><div class="label">Extracted</div><div class="value">${fmtBytes(st.extracted_bytes || 0)}</div></div>
    <div class="card"><div class="label">Imports</div><div class="value">${fmtBytes(st.imports_bytes || 0)}</div></div>`;
  const files = st.files || [];
  $("#storage-files").innerHTML = files.length
    ? `<table><thead><tr><th>Path</th><th>Size</th><th>Modified</th></tr></thead><tbody>${files
        .map((f) => `<tr><td class="mono">${esc(f.path)}</td><td>${fmtBytes(f.size)}</td><td>${esc(f.mtime || "")}</td></tr>`)
        .join("")}</tbody></table>`
    : `<p class="muted">Файлов пока нет.</p>`;
}

async function loadDocs(q) {
  const query = q != null ? q : (($("#doc-search") && $("#doc-search").value.trim()) || "");
  const qs = new URLSearchParams({ limit: "100", offset: "0" });
  if (query) qs.set("q", query);
  const data = await api(`/api/documents?${qs}`);
  state.docs = data.items || [];
  state.docsMeta = data;
  renderDocs();
}

async function loadQuality() {
  try {
    const [level, runs] = await Promise.all([
      api("/api/admin/quality/level"),
      api("/api/admin/quality/runs?limit=15"),
    ]);
    state.quality = level;
    state.qualityRuns = runs.items || [];
    renderQuality();
  } catch (e) {
    if (e.status === 401) showLogin(true);
    else toast(e.message, "bad");
  }
}

function renderQuality() {
  const q = state.quality || {};
  $("#quality-metrics").innerHTML = `
    <div class="card"><div class="label">Уровень</div><div class="value">${esc(q.level || "—")}</div></div>
    <div class="card"><div class="label">Score</div><div class="value">${q.score != null ? Number(q.score).toFixed(2) : "—"}</div></div>
    <div class="card"><div class="label">Порог</div><div class="value">${q.threshold != null ? Number(q.threshold).toFixed(2) : "0.55"}</div></div>
    <div class="card"><div class="label">Статус</div><div class="value">${esc(q.status || "—")}${q.stale ? " · stale" : ""}</div></div>
    <div class="card"><div class="label">Документы</div><div class="value">${q.documents_active ?? "—"}</div></div>
    <div class="card"><div class="label">Offline</div><div class="value">${q.offline !== false ? "yes" : "no"}</div></div>`;
  if ($("#quality-auto")) $("#quality-auto").checked = q.auto_improve !== false;
  const runs = state.qualityRuns || [];
  $("#quality-runs").innerHTML = runs.length
    ? `<table><thead><tr><th>Status</th><th>Score</th><th>Level</th><th>Attempt</th><th>Detail</th></tr></thead><tbody>${runs
        .map(
          (r) => `<tr>
      <td><span class="pill">${esc(r.status)}</span></td>
      <td>${r.score != null ? Number(r.score).toFixed(2) : "—"}</td>
      <td>${esc(r.level || "—")}</td>
      <td>${esc(r.attempt)}</td>
      <td class="muted">${esc((r.detail || "").slice(0, 120))}</td>
    </tr>`
        )
        .join("")}</tbody></table>`
    : `<p class="muted">Прогонов пока нет.</p>`;
}

async function pollQualityRun(runId) {
  const el = $("#quality-run-status");
  for (let i = 0; i < 600; i++) {
    const r = await api(`/api/admin/quality/runs/${runId}`);
    if (el) el.textContent = `${r.status} · attempt ${r.attempt}/${r.max_attempts} · score=${r.score != null ? Number(r.score).toFixed(2) : "—"} · ${r.detail || ""}`;
    if (["ok", "failed", "cancelled", "superseded"].includes(r.status)) {
      await loadQuality();
      toast(r.status === "ok" ? `Качество: ${r.level} (${Number(r.score).toFixed(2)})` : `Quality: ${r.status}`, r.status === "ok" ? "ok" : "bad");
      return r;
    }
    await new Promise((res) => setTimeout(res, 2000));
  }
}

function renderModels() {
  const m = state.models || {};
  $("#active-model").textContent = m.active || "—";
  const list = m.models || [];
  $("#model-list").innerHTML = list.length
    ? list
        .map(
          (x) => `<div class="card model-row">
      <div><strong>${esc(x.name)}</strong><div class="muted">${esc(x.size || "")}</div></div>
      <button class="btn ${m.active === x.name ? "ghost" : "primary"}" data-activate="${esc(x.name)}" ${m.active === x.name ? "disabled" : ""}>${m.active === x.name ? "active" : "activate"}</button>
    </div>`
        )
        .join("")
    : `<p class="muted">Модели не найдены (offline: нужен локальный GGUF/уже скачанный tag).</p>`;
  $$("[data-activate]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await api("/api/admin/models/activate", { method: "POST", body: { name: b.dataset.activate } });
        toast(`Активна: ${b.dataset.activate}`, "ok");
        refreshTab();
      } catch (e) {
        toast(e.message, "bad");
      }
    })
  );
}

function renderDocs() {
  const meta = state.docsMeta || {};
  const corpus = meta.corpus || {};
  if ($("#docs-corpus-metrics")) {
    $("#docs-corpus-metrics").innerHTML = `
      <div class="card"><div class="label">В хранилище</div><div class="value">${corpus.documents_active ?? meta.total ?? "—"}</div></div>
      <div class="card"><div class="label">Чанки</div><div class="value">${corpus.chunks ?? "—"}</div></div>
      <div class="card"><div class="label">Найдено</div><div class="value">${meta.total ?? (state.docs || []).length}</div></div>`;
  }
  const rows = state.docs || [];
  $("#doc-table").innerHTML = rows.length
    ? `<table><thead><tr><th>Name</th><th>Status</th><th>Version</th><th></th></tr></thead><tbody>${rows
        .map(
          (d) => `<tr>
      <td>${esc(d.original_name || d.id)}</td>
      <td><span class="pill">${esc(d.status || "")}</span></td>
      <td class="mono">${esc(String(d.active_version_id || "—").slice(0, 8))}</td>
      <td><button class="btn ghost" data-del="${d.id}">delete</button></td>
    </tr>`
        )
        .join("")}</tbody></table>`
    : `<p class="muted">Документов нет — загрузите файл или импортируйте папку.</p>`;
  $$("[data-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await api(`/api/documents/${b.dataset.del}`, { method: "DELETE" });
        toast("Удалено", "ok");
        refreshTab();
      } catch (e) {
        toast(e.message, "bad");
      }
    })
  );
}

async function refreshInbox() {
  const sub = ($("#inbox-subdir") && $("#inbox-subdir").value.trim()) || "";
  const q = sub ? `?subdir=${encodeURIComponent(sub)}` : "";
  const data = await api(`/api/documents/inbox${q}`);
  if ($("#inbox-hint")) {
    $("#inbox-hint").textContent = `Папка импорта: ${data.host_hint || data.inbox} (локальный диск)`;
  }
  const files = data.files || [];
  $("#inbox-files").innerHTML = files.length
    ? `<strong>${files.length} файл(ов)</strong><ul style="margin:0.4rem 0 0;padding-left:1.1rem;max-height:8rem;overflow:auto">${files
        .slice(0, 40)
        .map((f) => `<li class="mono">${esc(f.relative_path)} · ${fmtBytes(f.size)}</li>`)
        .join("")}${files.length > 40 ? "<li>…</li>" : ""}</ul>`
    : `<span>В папке нет PDF/DOCX/TXT.</span>`;
  const st = data.import || {};
  if (st.running) {
    $("#inbox-import-status").textContent = `Импорт… ${st.done + st.skipped + st.failed}/${st.total}`;
  } else if (st.batch_id) {
    $("#inbox-import-status").textContent = `Последний: ok=${st.done} skip=${st.skipped} fail=${st.failed}`;
  } else $("#inbox-import-status").textContent = "";
}

async function pollImportStatus() {
  for (let i = 0; i < 120; i++) {
    const st = await api("/api/documents/import-folder/status");
    if (!st.running) {
      $("#inbox-import-status").textContent = `Готово: ok=${st.done} skip=${st.skipped} fail=${st.failed}`;
      return st;
    }
    $("#inbox-import-status").textContent = `Импорт… ${st.done + st.skipped + st.failed}/${st.total}`;
    await new Promise((r) => setTimeout(r, 1500));
  }
}

async function trySession() {
  try {
    const me = await api("/api/auth/me");
    state.user = me;
    $("#user-label").textContent = me.login || "admin";
    showLogin(false);
    setTab("chat");
  } catch {
    showLogin(true);
  }
}

function wire() {
  $$(".nav-btn").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: { login: $("#login-user").value.trim(), password: $("#login-pass").value },
      });
      toast("Вход выполнен", "ok");
      await trySession();
    } catch (err) {
      toast(err.message || "Ошибка входа", "bad");
    }
  });

  $("#btn-logout").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    showLogin(true);
  });

  $("#btn-refresh").addEventListener("click", () => refreshTab());

  $("#btn-new-chat").addEventListener("click", async () => {
    try {
      const s = await api("/api/chats", { method: "POST", body: { title: "Новый чат" } });
      state.activeChatId = s.id;
      state.messages = [];
      await loadChats();
      renderChatLog();
      toast("Новый чат", "ok");
    } catch (e) {
      toast(e.message, "bad");
    }
  });

  $("#chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#chat-q").value.trim();
    if (!q) return;
    $("#chat-q").value = "";
    state.messages.push({ role: "user", content: q });
    renderChatLog();
    try {
      const r = await api("/api/chat", {
        method: "POST",
        body: { question: q, session_id: state.activeChatId || null },
      });
      state.activeChatId = r.session_id;
      state.messages.push({
        role: "assistant",
        content: r.answer || "",
        citations: r.citations || [],
        outcome: r.outcome,
        model: r.model,
      });
      await loadChats();
      renderChatLog();
    } catch (err) {
      toast(err.message, "bad");
      state.messages.pop();
      renderChatLog();
    }
  });

  $("#upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.append("file", $("#doc-file").files[0]);
    try {
      const r = await api("/api/documents", { method: "POST", body: fd });
      toast(`Uploaded job ${r.job_id}`, "ok");
      refreshTab();
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  $("#btn-inbox-refresh")?.addEventListener("click", async () => {
    try {
      await refreshInbox();
      toast("Список папки обновлён", "ok");
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  let importPreview = null;

  function fillPackSelect(options, selectedId) {
    const sel = $("#pack-existing-select");
    if (!sel) return;
    sel.innerHTML = (options || [])
      .map(
        (o) =>
          `<option value="${esc(o.pack_id)}" data-prefix="${esc(o.prefix)}" data-desc="${esc(o.description || o.label || "")}" ${
            o.pack_id === selectedId ? "selected" : ""
          }>${esc(o.prefix)} — ${esc(o.label || o.pack_id)}</option>`
      )
      .join("");
    updatePackExistingDesc();
  }

  function updatePackExistingDesc() {
    const sel = $("#pack-existing-select");
    const desc = $("#pack-existing-desc");
    if (!sel || !desc) return;
    const opt = sel.selectedOptions && sel.selectedOptions[0];
    desc.textContent = opt ? opt.dataset.desc || "" : "";
  }

  function syncPackNewHint() {
    const id = ($("#pack-new-id")?.value || "").trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_");
    const hint = $("#pack-new-prefix-hint");
    if (hint) hint.textContent = id ? `${id}_` : "…_";
  }

  function setPackModeUI(mode) {
    const ex = $("#pack-mode-existing");
    const nw = $("#pack-mode-new");
    if (ex) ex.checked = mode === "existing";
    if (nw) nw.checked = mode === "new";
    const sel = $("#pack-existing-select");
    const nid = $("#pack-new-id");
    const nlab = $("#pack-new-label");
    if (sel) sel.disabled = mode !== "existing";
    if (nid) nid.disabled = mode !== "new";
    if (nlab) nlab.disabled = mode !== "new";
  }

  $("#pack-existing-select")?.addEventListener("change", updatePackExistingDesc);
  $("#pack-new-id")?.addEventListener("input", syncPackNewHint);
  $("#pack-mode-existing")?.addEventListener("change", () => setPackModeUI("existing"));
  $("#pack-mode-new")?.addEventListener("change", () => setPackModeUI("new"));

  $("#btn-inbox-preview")?.addEventListener("click", async () => {
    try {
      const sub = ($("#inbox-subdir") && $("#inbox-subdir").value.trim()) || "";
      const r = await api("/api/documents/import-folder/preview", {
        method: "POST",
        body: { subdir: sub, recursive: true },
      });
      if (r.status === "empty") {
        toast("В папке нет подходящих файлов", "bad");
        $("#import-pack-panel").style.display = "none";
        importPreview = null;
        return;
      }
      importPreview = r;
      const sug = r.suggestion || {};
      const panel = $("#import-pack-panel");
      if (panel) panel.style.display = "block";
      const reasonMap = {
        filename_prefix: "по именам файлов",
        subdir_alias: "по имени подпапки",
        content_match: "по содержимому текстов",
        existing_pack_id: "категория уже есть",
        new_subdir: "новая тема по имени папки",
      };
      const why = reasonMap[sug.reason] || sug.reason || "";
      $("#import-pack-suggest").textContent =
        `Найдено файлов: ${r.total}. Рекомендация: ${sug.suggested_mode === "new" ? "создать новую" : "взять существующую"} «${sug.pack_id}» (${sug.prefix}) — ${why}.`;
      fillPackSelect(r.options || sug.options || [], sug.suggested_mode === "existing" ? sug.pack_id : (r.options?.[0]?.pack_id || ""));
      const pn = r.proposed_new || sug.proposed_new || {};
      if ($("#pack-new-id")) $("#pack-new-id").value = pn.pack_id || sug.pack_id || "";
      if ($("#pack-new-label")) $("#pack-new-label").value = pn.label || sug.label || "";
      syncPackNewHint();
      setPackModeUI(sug.suggested_mode === "new" ? "new" : "existing");
      toast("Выберите категорию и нажмите «Загрузить»", "ok");
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  $("#btn-inbox-preview-cancel")?.addEventListener("click", () => {
    importPreview = null;
    const panel = $("#import-pack-panel");
    if (panel) panel.style.display = "none";
  });

  $("#btn-inbox-import-confirm")?.addEventListener("click", async () => {
    try {
      if (!importPreview) {
        toast("Сначала нажмите «Подготовить импорт»", "bad");
        return;
      }
      const sub = ($("#inbox-subdir") && $("#inbox-subdir").value.trim()) || "";
      const mode = $("#pack-mode-new")?.checked ? "new" : "existing";
      let packId = "";
      let packLabel = "";
      if (mode === "existing") {
        packId = $("#pack-existing-select")?.value || "";
        const opt = $("#pack-existing-select")?.selectedOptions?.[0];
        packLabel = opt ? opt.textContent.split("—").slice(1).join("—").trim() : "";
        if (!packId) {
          toast("Выберите префикс из списка", "bad");
          return;
        }
      } else {
        packId = ($("#pack-new-id")?.value || "").trim();
        packLabel = ($("#pack-new-label")?.value || "").trim();
        if (!packId) {
          toast("Укажите идентификатор новой категории", "bad");
          return;
        }
      }
      const r = await api("/api/documents/import-folder", {
        method: "POST",
        body: {
          subdir: sub,
          recursive: true,
          skip_existing: true,
          pack_mode: mode,
          pack_id: packId,
          pack_label: packLabel || null,
        },
      });
      if (r.status === "empty") {
        toast("В папке нет подходящих файлов", "bad");
        return;
      }
      const p = r.pack || {};
      toast(`Загрузка: ${r.total} · ${p.prefix || packId}`, "ok");
      $("#inbox-import-status").textContent = `Категория: ${p.pack_id} (${p.prefix}) · ${p.decision}`;
      await pollImportStatus();
      importPreview = null;
      const panel = $("#import-pack-panel");
      if (panel) panel.style.display = "none";
      refreshTab();
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  $("#btn-corpus-packs")?.addEventListener("click", async () => {
    try {
      const r = await api("/api/admin/corpus-packs");
      const el = $("#corpus-packs-view");
      if (el) {
        el.style.display = "block";
        el.textContent = JSON.stringify(r, null, 2);
      }
      toast("Категории загружены", "ok");
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  $("#pull-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/admin/models/pull", { method: "POST", body: { name: $("#pull-name").value.trim() } });
      toast("Pull started", "ok");
      if (r.job_id) pollModelJob(r.job_id).catch(() => {});
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  $("#gguf-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.append("name", $("#gguf-name").value.trim());
    fd.append("file", $("#gguf-file").files[0]);
    try {
      const r = await api("/api/admin/models/import-gguf", { method: "POST", body: fd });
      toast("GGUF import started", "ok");
      if (r.job_id) pollModelJob(r.job_id).catch(() => {});
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  $("#url-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const sha = ($("#url-sha") && $("#url-sha").value.trim()) || null;
      const body = { name: $("#url-name").value.trim(), url: $("#url-link").value.trim() };
      if (sha) body.sha256 = sha;
      const r = await api("/api/admin/models/import-url", { method: "POST", body });
      toast("URL import started", "ok");
      if (r.job_id) pollModelJob(r.job_id).catch(() => {});
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  async function purgeAllChats() {
    if (!confirm("Удалить ВСЕ чаты и сообщения? Это нельзя отменить.")) return;
    const r = await api("/api/admin/data/purge-chats", { method: "POST", body: { confirm: true } });
    state.sessionId = null;
    state.messages = [];
    toast(`Чаты очищены: ${r.removed_sessions ?? 0}`, "ok");
    await loadChats();
    renderChatLog();
  }

  $("#btn-purge-chats")?.addEventListener("click", async () => {
    try {
      await purgeAllChats();
    } catch (err) {
      toast(err.message, "bad");
    }
  });
  $("#btn-purge-chats-storage")?.addEventListener("click", async () => {
    try {
      await purgeAllChats();
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  async function resetAllRagStorage() {
    if (
      !confirm(
        "Очистить ВСЁ хранилище и чаты?\n• удалятся чаты, индекс документов, scratch, Qdrant\n• навыки/умения/ошибки (docs/SKILLS|ERRORS|ABILITIES) СОХРАНЯТСЯ\n• затем загрузите новый пакет inbox с уникальным prefix (classic_, wildlife_, …)"
      )
    ) {
      return;
    }
    const r = await api("/api/admin/data/reset-rag", {
      method: "POST",
      body: {
        confirm: true,
        chats: true,
        documents: true,
        scratch: true,
        qdrant: true,
        clear_retrieval_prefix: true,
      },
    });
    state.sessionId = null;
    state.messages = [];
    toast(
      `Хранилище очищено: docs=${r.documents_tombstoned ?? 0}, chunks=${r.chunks_removed ?? 0}, chats=${r.chats_removed ?? 0}. Знания сохранены.`,
      "ok"
    );
    await loadChats();
    renderChatLog();
    refreshTab();
  }

  $("#btn-reset-rag")?.addEventListener("click", async () => {
    try {
      await resetAllRagStorage();
    } catch (err) {
      toast(err.message, "bad");
    }
  });
  $("#btn-reset-rag-chat")?.addEventListener("click", async () => {
    try {
      await resetAllRagStorage();
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  $("#btn-cleanup").addEventListener("click", async () => {
    try {
      const r = await api("/api/admin/storage/cleanup", { method: "POST", body: { confirm: true } });
      toast(r.message || "Cleanup done", "ok");
      refreshTab();
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  $("#btn-cleanup-tomb")?.addEventListener("click", async () => {
    try {
      const r = await api("/api/admin/storage/cleanup", {
        method: "POST",
        body: { confirm: true, tombstoned: true },
      });
      toast(`Tombstones removed: ${r.removed ?? 0}`, "ok");
      refreshTab();
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  $("#password-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const a = $("#pw-new").value;
    const b = $("#pw-new2").value;
    if (a !== b) {
      toast("Пароли не совпадают", "bad");
      return;
    }
    try {
      await api("/api/auth/change-password", {
        method: "POST",
        body: { current_password: $("#pw-current").value, new_password: a },
      });
      $("#password-form").reset();
      toast("Пароль изменён", "ok");
    } catch (err) {
      toast(err.message, "bad");
    }
  });

  $("#btn-doc-search")?.addEventListener("click", async () => {
    try {
      await loadDocs();
      toast("Поиск обновлён", "ok");
    } catch (err) {
      toast(err.message, "bad");
    }
  });
  $("#doc-search")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      loadDocs().catch((err) => toast(err.message, "bad"));
    }
  });

  $("#btn-quality-refresh")?.addEventListener("click", () => loadQuality());
  $("#quality-auto")?.addEventListener("change", async (e) => {
    try {
      await api("/api/admin/quality/auto-improve", {
        method: "POST",
        body: { enabled: !!e.target.checked },
      });
      toast(e.target.checked ? "Автоусиление включено" : "Автоусиление выключено", "ok");
    } catch (err) {
      toast(err.message, "bad");
    }
  });
  $("#btn-quality-run")?.addEventListener("click", async () => {
    try {
      const r = await api("/api/admin/quality/runs", {
        method: "POST",
        body: { threshold: 0.55, max_attempts: 8, auto_synth_if_empty: true },
      });
      toast("Quality run started (offline)", "ok");
      pollQualityRun(r.run_id).catch(() => {});
    } catch (err) {
      toast(err.message, "bad");
    }
  });
  $("#btn-quality-synth")?.addEventListener("click", async () => {
    try {
      const r = await api("/api/admin/quality/eval-set/synthesize", {
        method: "POST",
        body: { limit: 10 },
      });
      toast(`Синтез: +${r.added} (docs ${r.docs_considered})`, "ok");
      loadQuality();
    } catch (err) {
      toast(err.message, "bad");
    }
  });
  $("#btn-quality-import")?.addEventListener("click", async () => {
    try {
      const raw = ($("#quality-eval-json") && $("#quality-eval-json").value) || "[]";
      const items = JSON.parse(raw);
      if (!Array.isArray(items)) throw new Error("Ожидается JSON-массив");
      const r = await api("/api/admin/quality/eval-set/import", {
        method: "POST",
        body: { items, replace: false },
      });
      toast(`Eval import: +${r.added}, total=${r.total}`, "ok");
      loadQuality();
    } catch (err) {
      toast(err.message || String(err), "bad");
    }
  });
}

wire();
trySession();
