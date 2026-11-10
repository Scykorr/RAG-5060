# Progress

## 2026-11-10 — Graph connectivity index (slice 1) steps 2–3

- task_id: T-GRAPH-IDX-02 / T-GRAPH-IDX-03
- `apps/api/services/graph_index.py`: layers A (theme/author), B refers_to, C shared_entity, D lex_similar, E co_cited + JSON cache helpers
- Unit tests `tests/unit/test_graph_index.py` — 12 passed (no DB, no Qdrant)

## 2026-11-10 — Graph connectivity index (slice 1) step 1

- task_id: T-GRAPH-IDX-01
- Extracted `_norm_ru` / `_STOP` / `_ENTITY_DENY` / entity extractors → `apps/api/services/text_entities.py`
- `chat.py` imports shared helpers (behaviour unchanged); `tokenize()` for lexical layer
- Tests: `tests/unit/test_text_entities.py` + health — 6 passed

## 2026-11-10 — Graph Obsidian-like UX (Cytoscape, no D3)

- Verdict: keep Cytoscape; D3 rewrite not needed
- UX: degree-sized nodes, prefix colors, hide orphans, BFS chain highlight (depth 1–3), click→local focus, CTA «Анализ» when sparse edges
- Files: `apps/web/graph.js`, `index.html`, `styles.css`; skill S53

## 2026-11-10 — F5 refused: Windows proxy on :18080

- Root: F5 не ломает сайт; Windows↔WSL publish на :18080 сам мигает (WSL curl ок)
- Fix: nginx publish `:28080`; `RAG5060.exe` reverse-proxy слушает нативный `:18080` → WSL:28080
- Verify: 12× health 200 через proxy; UI открыт `http://127.0.0.1:18080/` — окно exe не закрывать
- ERRORS E-20261110-f5-wslrelay-flap; compose `RAG_DOCKER_UI_PORT`

## 2026-11-10 — Knowledge contract: apply + replay

- Усилен смысл `docs/{SKILLS,ERRORS,ABILITIES}` (+ зеркала `Обучение/docs/`): у каждой записи алгоритм применения/повтора
- SKILLS: колонка «Как применить» + S51 + индекс по темам
- ABILITIES: «Как применить» + Replay + Skills; запрет yes/pass без replay
- ERRORS: id / reproduce / replay / prevent / links; `fixed` только с replay
- AGENTS.md: секция «Контракт знаний (S51)»

## 2026-11-10 — Chat send harden + graph visual polish

- Live smoke: chat=200, graph nodes/status=200 (`SMOKE_CHAT_GRAPH_OK`) while stack healthy
- Chat UX: preflight health + one retry + «Думаю…»; message clarifies local API (no internet)
- Graph: animated cose/breadthfirst, hover neighborhood dim, select pulse, canvas ambient gradient
- Skills S49–S50, ability A17

## 2026-11-10 — Docs sync + offline chat clarification

- User: offline-only; error only on chat «Отправить»; server was stopped
- Clarified in SKILLS S46–S48, ABILITIES A16, ERRORS 2026-11-10, ROADMAP F12.9–F12.10 + R11: Failed to fetch ≠ internet; need running local stack; WSL memory 12GB
- Ritual reminder: always read/update both `docs/` and `Обучение/docs/` + roadmap/progress

## 2026-11-09 — Site up / API unreachable (stale wslrelay)

- Symptom: graph UI visible, toast «нет связи с API»; green «offline» pill was misleading (CDN-free, not link status)
- Fix: WSL `networkingMode=mirrored`; revive script; launcher WSL-IP fallback; UI `API ок/нет` pill + clearer error text

## 2026-11-09 — Login root-cause fix (stack overflow + WSL port)

- Root: Graph `window.api = () => api()` recursion killed every browser login (`Maximum call stack size exceeded`)
- Also: WSL publish `127.0.0.1:18080` unreachable from Windows after flap; launcher `--force-recreate nginx`; API/DB race
- Fix: `ragApi` export; open port bind; no force-recreate; `_wait_for_db`; login retries; browser verified (chat UI after Войти)
- Skills S41–S43, abilities A14–A15, ERRORS entry

## 2026-11-09 — Login reliability + UI port 18080

- Host UI: `http://127.0.0.1:18080` (`RAG_UI_PORT`); launcher strict 3× JSON health; login never silent (`aria-disabled`, toasts, health poll)
- Faster API boot: ollama `service_started` only; skip bcrypt rehash every restart
- Verify: `scripts/verify-login-e2e.sh` → LOGIN_E2E_OK; skills S37–S40, abilities A14, ERRORS login/port entries; ROADMAP F12.5–F12.7

## 2026-11-09 — Document Graph Explorer (P0 MVP)

- Branch `feature/document-graph`: audit/ADR in `docs/graph/`, Alembic `20261109_0003`, `/api/graph/*`, Cytoscape vendor, SPA tab «Граф связей» (`/?tab=graph`)
- Detectors: SAME_FAMILY / EXPLICIT_REFERENCE / lexical SEMANTIC_SIMILAR (`token_jaccard_v1`); LLM OFF
- Smoke: sample.txt↔sample-b.txt edge+evidence; double rebuild idempotent (`edges_stored=1`); chat OK; pytest `tests/graph` 7 passed
- Gap: Qdrant still zero-vectors → `DEGRADED_NO_VECTOR_SEARCH`; no access_groups tables (tenant ACL as documents)

## 2026-11-06 — plan gap-fill (Admin UI Launcher)

Closed remaining items from `.cursor/plans/admin_ui_launcher_1e36b3e7.plan.md`:
- System panel + `/api/admin/logs` (safe job tail), compose/offline/hints on `/api/admin/system`
- Storage tombstone cleanup in UI
- Model Hub: URL progress, size limit, optional sha256, cancel job
- Launcher preflight disk/GPU + `RAG_DATA_ROOT`; `scripts/export-offline-images.sh`
- ROADMAP F11–F13 checkboxes marked done (static SPA accepted; React optional)

**Verify** (`artifacts/20261106-082053-post-plan`) — **passed** (`FAIL=0`)
- rebuilt `api` image; stack healthy on `:8080`
- admin system/logs/storage/dispatcher; activate `qwen3:8b` smoke OK
- UI System panel + tombstone/sha fields present
- S2: upload → ready → chat «12500 / НДС 20%» + citations

## 2026-11-06 — F14 Corpus Quality Loop

## 2026-11-06 — Query intent (произвольные RU-запросы)

- `apps/api/services/query_intent.py` + routing в `chat.py`: catalog / author_overview / summarize / extractive|cloze
- Smoke: «сколько Чехова…» → catalog, 77 названий; AGENTS.md секция «Chat: произвольные RU-запросы»
- Канон: S33–S34, A12; зеркала `Обучение/docs/*`

## earlier — quality loop

Implemented offline quality loop + doc search + incremental reload:
- `/api/documents?q=` + corpus counts; Storage corpus metrics
- `/api/admin/quality/*` (level, runs, eval import/synth, auto-improve)
- fingerprint stale on upload/import; quality on full corpus (prefix cleared during eval)
- Admin tab «Качество»; ROADMAP F14

**Verify** (`artifacts/20261106-111729-quality`) — API/UI smoke **FAIL=0** (doc search, A→B reload, level offline).  
**Score fix** (`artifacts/20261106-112220-quality-fix`) — quality run **ok / strong / score=1.0** after clearing pack prefix during eval.

## 2026-11-03 — S1b → S2 → S3 verified

**S1b** (`artifacts/20261103-101124-s1b`) — verified
- Admin `/api/admin/system` + `/storage`; UI static `/` `/app.js` `/styles.css`
- Model Hub: `qwen3:8b` activated (smoke `OK`); launcher binaries + `status`
- Stack healthy on `127.0.0.1:8080`

**S2** (`artifacts/20261103-101308-s2`) — verified
- login → upload `sample.txt` → job `ready` → chat with citation
- Answer: invoice 12500 RUB / VAT 20% `[1]`; storage originals present

**S3** (`artifacts/20261103-102457-s3`) — verified (MVP slice)
- Tombstone delete + 404; deleted doc not listed; unknown UUID 404
- `POST /api/jobs/{id}/retry` (attempts++)
- API container restart + health restore
- `scripts/backup-mvp.sh` → `D:\RAG5060\backups\<stamp>\` (pg dump + ragdata + manifest)
- Retrieval stopwords → negative question `not_found`

**Constraint**
- C: ≥50 GB; heavy data on D: / WSL VHDX; `OLLAMA_NO_CLOUD=1`
