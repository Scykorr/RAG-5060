## 2026-11-12 — Slice 3b (Obsidian Graph polish, sigma 3)

- Preflight: 2d overview labels + `kind=unthemed` («Без темы») OK
- Step 0: sigma **3.0.2** UMD + slim d3-force stack (full d3.min.js dropped from SPA)
- Step 1: arrow edges, hover reducers, theme-aware label plates, live ◐ `setSetting`
- Steps 2–4: Obsidian sizes/palette/top-k, layout pull+clusters, **node drag**, settings toggles/↺
- Step 5: perf 1500/5000 ≈60 FPS; 3000 hover 37 → hideEdgesOnMove ≥1200
- Report `artifacts/20261112-graph-s3b/`; knowledge **S62/A27**; ROADMAP **F15.6d**
- Note: UI ↻ rebuild can block API worker — restart api if health hangs

## 2026-11-12 — Slice 2d (homoglyph / inter / unthemed / labels)

- Homoglyph Latin→Cyrillic in `clean_chunk` + tokenize; INDEX_VERSION **17**; en untouched
- Inter-theme: unified cut/min(vol); grid picks theme_resolution **1.3** (max_inter≈0.20, purity 0.42)
- Proper: play-speaker mid-cap + patronymic/cross-work; common nouns kept
- «Без темы»: top-level `kind=unthemed`; not a vote target; low-conf keeps secondary
- Labels: stem-grounded, reject «A и B», theme≠topic, 3-term fallback without work title
- α=0.6 / same_author_factor=0.35 / vec cache rev `2d-2` unchanged otherwise
- Report `artifacts/20261112-075149-topics/` **PASS** (groups 3/3, noop 23ms, wo_llm≈2.2s)
- Knowledge: **S61**, **A26**, E-topics-inter-metric / homoglyph; ROADMAP **F04.18i**

## 2026-11-12 — Slice 3 (Obsidian Graph UI)

- Vendor: graphology 0.25.4 + sigma 2.4.0 + d3 7.9 (offline under `apps/web/vendor/`); adapter `js/graph/graph-view.js`
- Shell: levels overview/theme/topic/local, hash crumbs, settings parity, Obsidian look (`#1e1e1e` / `#7f6df2`), position cache, rebuild/stale
- Smoke: overview 31/217 in 37 ms, 61 FPS; theme 97/730; local 122/941; **0 external** Network
- Fix: sigma `nodeKind` not `type` (E-20261112-sigma-node-type); API image rebuilt for level routes
- Knowledge: **S60**, **A25**; ROADMAP **F15.6c**; report `artifacts/20261112-graph-s3/`

## 2026-11-12 — Slice 2c (proper nouns, weak texts, grounded labels, cache)

- Proper/dialogue cap + entity person/place ban; weak/short → «Без темы» (unthemed≤10%); theme_resolution 1.3; grounded LLM labels; vec_cache + stage_ms
- α=0.6 / same_author_factor=0.35 unchanged
- Report `artifacts/20261112-064335-topics/` **PASS** (purity 0.36, groups 3/3, recluster 3.2s, overlap 1.0)
- Knowledge: **S59**, **A24**, E-topics-proper / labels-slow; ROADMAP **F04.18h**; mirrors `Обучение/docs/*`

## 2026-11-11 — Slice 2b (thematic vectors, not author folders)

- Report: `artifacts/20261112-052018-topics/`
- `topic_vectors`: author-entropy ≥0.5 & ≥3 authors, style stops + auto-style, ru gerunds, noun boost, leave-one-out debias α=0.6, term_cap/top_k for speed
- Cluster graph: `topic_knn` (k=10, cross-author quota), `same_author_factor` **0.35**; `work_lex_similar` weight 0 (viz only)
- Quality: lex_same_author_share **0.3458** (was 0.978); theme mean purity **0.3682** (≤0.75); 100% pure themes **0**; expectations **3/3**
- Labels: thematic c-TF-IDF + Ollama `qwen3:8b` (fallback terms); secondary_topics in API
- Perf: noop **14ms**; recluster id overlap **1.0**; full w/ LLM ~144s (cluster-only ~70s); incremental **21ms** after graph reuse
- Config: `configs/graph_topics_expectations.json`, `topic_style_stopwords` in lexicon; pytest topic unit **22 passed**
- No α-grid needed (purity already ≤0.75 at α=0.6 / factor=0.35)
- Knowledge: **S58**, **A23**, E-20261112-topics-author-folder / report-hang; ROADMAP **F04.18f/g**, **F15.6b**; mirrors `Обучение/docs/*` + RESULTS

## 2026-11-11 — Slice 2 steps 1–7 (themes/topics)

- INDEX_VERSION stays **16** (collection is an alias of `in_theme`; no index rebuild)
- `same_author_factor` **0.6**; lex 0.45 / refers 1.9 / shared_entity 1.8; topic_resolution 2.6; theme_resolution 1.0
- Report `artifacts/20261111-104731-topics/`: works **371**, topics **19**, themes **4**, «Без темы» **6** (1.6%)
- Author purity: content topics 17, 100% pure **8** (share **0.4706**), mean 0.9095, half-rule **pass**
- Stability: hold out 25% (92 works) keeps the remaining works' topic ids at **100%**; +5 works stay incremental
- Topics build **11.1s** after a warm index (noop load 3.0s); `pytest tests/unit/test_graph_topics*.py` **17 passed**
- S57/A22. API: `GET /api/graph/overview?view=levels`, `/themes/{id}`, `/topics/{id}`, `/local/{id}`, `/search`, `/nodes/{id}`; non-UUID `/edges/{id}`; `POST /rebuild?scope=index|topics|all`
- Labels are mixed. War, poetry, and faith read as themes («адъютант, пехотных — Набег»; «хладный, забвенье, вдохновенье»; «учения, религиозного — Царство Божие»). The Chekhov theme is still an author folder (purity 1.0, speech «нешто, тово»). The large multi-author prose theme is labeled with style words («дескать»), not a subject.

## 2026-11-11 — Slice 2 step 0 (1e leftovers)

- INDEX_VERSION=**16**; report `artifacts/20261111-101906-graph-index/`
- refers_to estimated_precision **90%** (n=29 auto-judged); person titles quoted-only; Melitel/competing-name rules
- shared_entity: work-level df, cross-author ≥2 strong; «Иван Федорович» alone no cross-author edge
- fr/de → `other` (not en-lex); mixed Latin islands gated; en lex no longer flooded with French
- stage directions stripped structurally; top-30 lex terms clean of целует/уходят/…
- perf: warm full **74s**, noop **48ms**, +10 **3.5s**; healed corpus `fingerprint` shadow bug
- overrides_applied **33**; isolated_works **5**; pytest graph unit **93 passed**
- Next: steps 1–7 topics/themes + level API

## 2026-11-11 — Graph 1e-M multilang (ru+en)

- INDEX_VERSION=14; S56/A21; F04.18e
- `graph_lang.py`: language/doc_kind, EN stem, identifiers (RFC/ISO/TLS/code), literary gate
- Lexicon nested `{ru,en,shared}`; per-lang IDF/lex (no cross-lang lex; note → local Ollama later)
- `shared_entity` cross_lang for language-agnostic ids; technical `edition_of` + tech refers
- Tests: `test_graph_multilang.py` 8 + full graph unit **84 passed**; fixtures `tests/fixtures/graph_multilang/`
- Report: `artifacts/20261111-072215-graph-index/` — lang `{ru:690,mixed:10,en:2}`, doc_kind fiction-heavy; cross_lang=0 on classic (EN only in fixtures); isolated_works **1.3%**; good/bad green
- Artifact cache now keyed by `index_version` (invalidate on extractor changes)

## 2026-11-11 — Graph 1e acceptance report

- Report: `artifacts/20261111-062930-graph-index/` vs `20261111-050958` (INDEX_VERSION=11)
- isolated_works_no_cocited: **5 / 386 (1.3%)** (was 105 / 38%); weak 5/2814
- regression good: **all found**; bad: **all absent**
- duplicates 28 + contained_in 97; Anna frag=165 one work_key; co_cited accepted=0
- built_ms full≈139s (**over** ≤70s target); noop≈1.9s (**pass** ≤2s)
- pytest graph unit: **76 passed**
- refers_to sample n=40 in report (manual ≥90% — fill ok?); overrides stub 33 no-author

## 2026-11-11 — Graph 1e lex recall + dup speed

- Dual signatures (tf·idf ∪ residual), same-author soft kNN, cross-theme stems from lexicon
- Isolation metrics on canonical docs only; faster blake2b/int shingles + minhash
- Regression good (Gandhi / безумие / Воскресение↔Карамазовы) green; bad clean
- INDEX_VERSION=11; pytest graph unit 76 passed

## 2026-11-11 — Graph 1e report status

- Report: `artifacts/20261111-055631-graph-index/` vs `20261111-050958`
- duplicates 28 + contained_in 97 (contains=99); co_cited filtered (accepted=0)
- isolated_works_no_cocited share=0.38 (target ≤0.10) — **failed**; weak_edges 2/183 OK
- regression good: Gandhi / О безумии still absent; bad clean
- built_ms≈504s (**failed** ≤70s) — shingle hashing cost; noop unit OK
- pytest graph unit: 76 passed

## 2026-11-11 — Graph 1e E1 fix (shingle hashes)

- Full shingle hashes (n=5); compare to top-8 longest per work; eb→duplicate, fragments→contained_in
- Report `artifacts/20261111-054754-graph-index/` (pre-fix); re-report pending
- weak_floor default 0.12; unit duplicates green

## 2026-11-11 — Graph 1e E3–E8

- E3 co_cited: drop >N / alphabetical / list-query; weight×0.5; isolated_*_no_cocited
- E4 shared_entity: strong only df≤3; exclude author FIO/title; cross-author ≥2 strong
- E5 work_mentions_author weight fix; ambiguous Gorky needs name/initials
- E6 refers: question/person titles quoted-only; ambiguous/foreign → confidence=low ×0.3
- E7 configs/graph_overrides.json; E8 noop rebuild when fp+params unchanged
- pytest graph unit: 76 passed

## 2026-11-11 — Graph 1e E2 (work-level lex)

- Work vectors: 1+log tf, IDF, df∈[2,30%], burstiness, mutual kNN, weak floor
- style_burst_hints + speech/stage excluded; INDEX_VERSION=10
- Tests: theme link, burstiness, weak floor

## 2026-11-11 — Graph 1e E1 (duplicates + contained_in)

- task_id: T-GRAPH-IDX-1E — in progress (E1 done)
- Minhash-128 / LSH; duplicate Jaccard≥0.8; contained_in coverage≥0.8; contains edges
- DF only on canonical; refers_to container→nested → contains; INDEX_VERSION=10
- Tests: Anna canonical/duplicate/contained; story-in-collection contains

# Progress

## 2026-11-11 — Lifecycle clarifications (F04.18c)

- params_hash includes `graph_lexicon.json` SHA1; atomic index + `current/<tenant>.json` (fail keeps previous)
- File lock `graph/locks/<tenant>.lock` + dead/timeout clear; stale after ingestion; `GRAPH_AUTO_REBUILD` (default false)
- Test 6 edition/volume + tenant isolation; CONNECTIVITY_LIFECYCLE.md updated; pytest graph unit 64 passed

## 2026-11-11 — Knowledge sync after 1d (S48/S51)

- S55 + A20; ERRORS E-20261111-{refers-partial-lower,lex-proper-names,report-dataroot}
- ROADMAP F04.18a/b + F15.6a checked; mirrors `Обучение/docs/*` synced

## 2026-11-11 — Graph connectivity 1d complete (report)

- task_id: T-GRAPH-IDX-1D-A — verified (unit + corpus report)
- Report: `artifacts/20261111-050958-graph-index/` vs baseline `20261110-133444` (v3)
- INDEX_VERSION=9; refers 640→51; shared 673→328; lex 664→449; no_author 48→33 (text_header=15)
- refers precision sample ≈90%; built_ms warm≈62s (cached=702); cold full≈98s
- Lifecycle Part B already in 7f0937d; unit lifecycle 6 passed; CONNECTIVITY_LIFECYCLE.md present
- Gandhi: absent on full corpus due to high-df shared stems after name drop (pair-only score≈0.60)

## 2026-11-10 — Graph connectivity 1d A6–A7 (duplicates + isolation report)

- task_id: T-GRAPH-IDX-1D-A — in progress (A1–A7 done)
- Shingle near-dupes within work_key → canonical / duplicate_of; df/stats from canonical only
- isolated_content vs isolated_structural + top-20 reasons; author degree separate from works
- Report: build_mode/lifecycle, refers sample×40, no-author preview; INDEX_VERSION=8
- Tests: 58 passed
- Next: full corpus report vs 133444 + lifecycle verify

## 2026-11-10 — Graph connectivity 1d A4–A5 (authors + text_header)

- task_id: T-GRAPH-IDX-1D-A — in progress (A1–A5 done)
- `graph_authors.py`: capitalized-only mentions; reject hyphen compounds / wrong given names / wrong initials; confidence high|medium; commentary zone ×0.3
- text_header: FIO in first ~10 lines → author_source=text_header (Herzen case)
- author_name_table surnames/initials in `configs/graph_lexicon.json`; INDEX_VERSION=7
- Tests: 43 passed
- Next: A6 duplicates + A7 report

## 2026-11-10 — Graph connectivity 1d A2–A3 (lex names + entity strength)

- task_id: T-GRAPH-IDX-1D-A — in progress (A1–A3 done)
- Lex: name forms from mid-cap ≥0.7 before stemming; diminutive/editorial/speech dropped; ≥3 shared; single-term >50% rejected
- Entities: lemma group (Анна/Анной); strong/weak/drop; ≥1 strong required; interjections/pronouns dropped
- Regression list: bad += Слабое сердце/Обручение Даши ↔ Отцы и дети; Мёртвые души → same_work check
- Tests: 36 passed; INDEX_VERSION=6
- Next: A4 mentions zones + A5 text_header

## 2026-11-10 — Graph connectivity 1d A1 (strict refers_to)

- task_id: T-GRAPH-IDX-1D-A — in progress (A1 done)
- `apps/api/services/graph_refers.py`: full-title token match; no lowercase; genre 0–2 tokens before; exact quote=title; one-word only quoted|genre; same-author / frequent titles need strong form; zone commentary ×0.3
- `layer_b_refers_to` wired to grefer; `multiword` from full title tokens; INDEX_VERSION=5
- Tests: `test_graph_refers.py` + graph_index — 29 passed
- Next: A2 lex names + A3 entities

## 2026-11-10 — Graph connectivity lifecycle (1d)

- task_id: T-GRAPH-IDX-1D — verified (unit)
- Spec: `docs/graph/CONNECTIVITY_LIFECYCLE.md`; S54; A19
- `configs/graph_lexicon.json` — language-only editable rules (offline)
- Per-doc artifacts under `graph/doc_cache/<tenant>/`; index key `tenant+fp+v+params_hash`
- `build_mode` full|incremental; lifecycle counts; background `schedule_rebuild` single-flight
- `POST /api/graph/index/rebuild` async by default (`sync=true` for tests); stats → building|ok|missing
- `reset-rag` invalidates indexes + doc caches
- Tests: `test_graph_index_lifecycle.py` + suite — 32 passed; INDEX_VERSION=4

## 2026-11-10 — Graph connectivity index slice 1c (complete)

- task_id: T-GRAPH-IDX-1C
- Text clean + mentions_author + strong refers + entity sentence-start + lex sans names + work merge/agg
- Report: `artifacts/20261110-133444-graph-index/graph_index_report.md`
- Metrics vs 1b (`20261110-123456`): refers 2602→640; shared 2268→673; lex 1487→664; works 416→406; no-author 54→48; built_ms 319570→45430
- Layer ms: scan_clean~15s, refers~25s, lex~3s, mentions~1.7s (bottleneck=refers phrase scan)
- Work merge: Анна Каренина eb+ch+фрагменты; Братья Карамазовы части; Война и мир тома; Мёртвые души тома
- lex regression: good 4/6 found (Мёртвые души merged=same work; Gandhi pair absent); bad 4/4 absent
- Tests: 26 passed; INDEX_VERSION=3

## 2026-11-10 — Graph connectivity index slice 1c step 2 (mentions_author + refers)

- task_id: T-GRAPH-IDX-1C
- `mentions_author`: capitalized/initialed author forms → `author:<key>`; self-mentions skipped; no refers_to on works via surname
- `refers_to`: titles only; one-word/common/inflected hits need strong form (quoted|capitalized|genre_marker|near_author); `match_form` in provenance
- Build path cleans chunks via `clean_chunk_for_graph` before all textual layers; INDEX_VERSION=3
- Work aggregation: `log1p(sum)/sqrt(frag_a*frag_b)` + top-k; `work_mentions_author`
- Tests: 23 passed (graph_index + graph_text_clean)

## 2026-11-10 — Graph connectivity index slice 1c step 1 (text clean)

- task_id: T-GRAPH-IDX-1C
- `apps/api/services/graph_text_clean.py`: `clean_chunk_for_graph` strips real corpus banners (`Автор/папка`+`Файл`, `Автор`+`Название/часть`, fetch-ru-corpus `Произведение/Источник/Фрагмент`) and early-chunk edition/title lines; returns `header_info` (author/title/file)
- Ingestion does not inject these labels — they live in source `.txt`; cleaner parses observed formats, not guessed regexes
- Tests: `tests/unit/test_graph_text_clean.py` — 6 passed
- Next: mentions_author + strong-form refers_to

## 2026-11-10 — Graph connectivity index slice 1b (cleanup)

- task_id: T-GRAPH-IDX-1B
- Filename parse: source tokens `ch`/`eb`, strip `Russian`/author from title, `fragment_no`, `work_key`, `part_of`
- refers_to: multiword/rare-proper only, weight≥2 or multiword, hub filter, no same-work
- shared_entity: edition stop, case bias, stem fold, work-level df cap
- lex_similar: fixed RU (stem+cosine+work df); prior bug = tiny TF-IDF product threshold on fragment df
- co_cited: live vs `*-smoke`/`ping` eval sessions; quality_loop does not persist messages
- Tests: 17 passed; report script expanded
- New report: `artifacts/20261110-123456-graph-index/graph_index_report.md`
- Metrics: refers_to 15578→2602; lex_similar 1→1487; works=416; isolated_docs=19 (2.7%); isolated_works=10

## 2026-11-10 — Graph connectivity index (slice 1) step 4

- task_id: T-GRAPH-IDX-04
- API: `POST/GET /api/graph/index/*` on existing graph router (tenant from session)
- `scripts/graph_index_report.py` → `artifacts/20261110-120700-graph-index/graph_index_report.md`
- Real corpus: 702 docs / 12041 chunks / ~21.6k edges; refers_to noisy (short titles); lex_similar≈1 (raise recall next)
- Unit tests: 18 passed

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
