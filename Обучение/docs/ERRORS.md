# Журнал ошибок (канон в docs/)

Агенты читают этот файл **перед** отладкой RAG. Зеркало: `Обучение/docs/ERRORS.md`.

## Зачем файл

Ошибка — не «история страданий», а **алгоритм решения**: узнать симптом → воспроизвести → применить fix → **replay**, что больше не ломается → закрепить prevent в Skills/Abilities.

Без `replay` статус `fixed` запрещён (только `mitigated` / `open` / `recurring`).

## Контракт записи

```
## YYYY-MM-DD — короткий заголовок
- id: E-YYYYMMDD-slug
- symptom: что видит пользователь / лог
- reproduce: как повторить симптом (до фикса)
- root_cause: почему
- fix: что сделали в коде/ops (конкретно)
- replay: как проверить, что фикс держится (команды / UI / ожидаемый выход)
- prevent: какой Skill/Ability/ритуал не даёт вернуться
- links: Sxx, Ayy, scripts/…
- status: open | mitigated | fixed | recurring
```

**Ритуал отладки:** Ctrl+F по symptom → если есть запись — выполнить `replay`/`fix` из неё, не изобретать заново → при новом инциденте дописать блок целиком.

---

## 2026-11-06 — Offline Python deps / multi-topic packs
- id: E-20261106-offline-wheels
- symptom: `pip install` на каждом запуске harness; риск смешения тем в одном индексе
- reproduce: чистый harness start без `vendor/python-wheels`; два корпуса без prefix
- root_cause: Dockerfile/scripts качали PyPI; один плоский индекс без prefix-политики
- fix: `vendor/python-wheels` + `scripts/vendor-python-wheels.sh`; Dockerfile `--no-index`; UI full reset сохраняет docs skills; comma prefixes `classic_,wildlife_`
- replay: `ls vendor/python-wheels` не пусто; build API без сети; chat с `classic_,wildlife_` не смешивает без политики
- prevent: S29, S27, A04, A09
- links: S29, S27, A04
- status: fixed (wheels один раз скачать онлайн)

## 2026-11-06 — Prefix was manual
- id: E-20261106-prefix-manual
- symptom: непонятно, как задаётся prefix; риск смешения категорий
- reproduce: import-folder без pack UI; prefix только в chat-eval-config
- root_cause: `retrieval_filename_prefix` только вручную
- fix: `corpus_packs.resolve_pack` при import-folder; stamp `prefix+filename`; sync active prefixes
- replay: UI preview → выбрать pack → import → имя файла с prefix; Storage counts по pack
- prevent: S31, S32, A10, A11
- links: corpus_packs, documents.py
- status: fixed

## 2026-11-06 — Residual non-classic after classic import
- id: E-20261106-residual-nonclassic
- symptom: 681 non-classic рядом с 700 classic_; `_manifest.txt` в индексе
- reproduce: import classic без полного reset после тестовых пакетов
- root_cause: повторный import / не полный reset; служебные файлы в корпусе
- fix: purge-chats; soft-delete non-classic; chunks purge; leave classic_; N6 deny-list `_manifest`
- replay: после reset+classic import — в индексе только `classic_*`; `_manifest` отсутствует
- prevent: S21, S30, A06, A07
- links: S21, S30
- status: fixed (smoke Anna Karenina quote OK)

## 2026-11-06 — Classic corpus bootstrap
- id: E-20261106-classic-bootstrap
- symptom: нужно ≥500 произведений; тестовые RuCoS/XQuAD засоряли retrieval
- reproduce: QA по classic на смешанном индексе
- root_cause: смешанный индекс + не было полного сброса
- fix: `reset-rag` + UI; корпус `classic_ru` (~700); prefix `classic_`; RU prompts
- replay: Storage classic≥500; A08 eval / attempt19
- prevent: S21, S22, A07
- links: A07, A08
- status: fixed

## 2026-11-06 — Classic QA lexical ceiling
- id: E-20261106-classic-lexical
- symptom: live contain ~0.40–0.44 (n=25); offline oracle needle+answer = 1.0
- reproduce: unique-needle sample без phrase-prefilter / с битым blank parser
- root_cause: lexical ILIKE без dense; blank parser мимо «Заполни пропуск»; OR-flood
- fix: phrase-prefilter; blank parser; RU stopwords; stem contain; backlog N1 dense
- replay: quality run thr=0.55 → contain ≥0.75 (attempt19); offline oracle 18/20
- prevent: S24, S25, S26, A08
- links: artifacts attempt19
- status: fixed

## 2026-11-06 — Classic QA low score @0.2
- id: E-20261106-classic-low-02
- symptom: contain 8/40 after full classic import
- reproduce: lexical LIMIT без term prefilter на полном корпусе
- root_cause: слепой LIMIT
- fix: SQL phrase/term prefilter
- replay: superseded → см. E-20261106-classic-lexical
- prevent: S24
- links: S24
- status: fixed (superseded)

## 2026-11-05 — Тест 3 cloze (итог)
- id: E-20261105-cloze
- symptom: live 0.45 vs offline 1.0
- reproduce: RuCoS live API; retrieve только cloze-tail
- root_cause: chunk without passage; highlight/quoted noise
- fix: full-doc retrieve + ranker; attempt10 @0.95
- replay: harness Тест 3 ≥0.95; A03
- prevent: S15–S18, A03
- links: RESULTS.md Тест 3
- status: fixed

## 2026-11-06 — Arbitrary RU query intents
- id: E-20261106-ru-intents
- symptom: «о творчестве Пушкина», «сколько Чехова», «изложение рассказа» не маршрутизировались
- reproduce: такие вопросы при одном extractive пути
- root_cause: нет intent-роутинга
- fix: `query_intent` (catalog/overview/summarize) + filename author catalog
- replay: «сколько Чехова в базе? названия» → catalog, ~77 titles; intent в ответе API
- prevent: S33, S34, A12
- links: `apps/api/services/query_intent.py`, AGENTS.md
- status: fixed

## 2026-11-09 — Graph / zero embeddings
- id: E-20261109-zero-embeddings
- symptom: Qdrant points с нулевыми векторами; cosine ANN бессмысленен
- reproduce: graph similarity на текущем Qdrant
- root_cause: `ingestion._maybe_qdrant_upsert` пишет `[0.0]*dim`
- fix: lexical `token_jaccard_v1` + `DEGRADED_NO_VECTOR_SEARCH`; не фейковать cosine
- replay: `scripts/smoke-graph.sh`; в evidence/status — DEGRADED, не cosine
- prevent: S36, A13; backlog N1
- links: graph detectors
- status: mitigated (N1 dense ещё backlog)

## 2026-11-09 — Login silent / «нет ответа» при «готово» в exe
- id: E-20261109-login-silent
- symptom: «Войти» ничего не делает; health «не готов» при «API готов»; F5 → refused на :8080
- reproduce: клик Войти на :8080 / disabled button / один flaky health
- root_cause: disabled глотает click; flaky health; nginx 502; конфликт порта 8080
- fix: кнопка всегда кликабельна + toast; poll health; лаунчер 3× `status=ok`; порт **18080**; ollama=`service_started`; skip bcrypt rehash
- replay: `scripts/verify-login-e2e.sh` → `LOGIN_E2E_OK` на :18080
- prevent: S37–S40, A14
- links: `scripts/verify-login-e2e.sh`
- status: fixed

## 2026-11-09 — Launcher окно сразу закрывается
- id: E-20261109-launcher-exit
- symptom: exe мигает; браузер на неготовый API
- reproduce: старый launcher без pause / openBrowser до health
- root_cause: process exit без pause; ранний openBrowser; `sudo docker`
- fix: pause Enter; strict health; prefer `docker` без sudo; `logs/launcher.log`
- replay: start exe → окно живёт → браузер после ready; лог пишется
- prevent: S39, A14
- links: apps/launcher
- status: fixed

## 2026-11-09 — Войти → stack overflow / Windows localhost dead
- id: E-20261109-api-recursion-port
- symptom: Войти тихо/не входит; WSL curl :18080 ок, Windows refused; Console `RangeError: Maximum call stack size exceeded`
- reproduce: login в браузере Windows после WSL flap; DevTools Console
- root_cause:
  1) `window.api = (...a) => api(...a)` → рекурсия
  2) publish `127.0.0.1:18080` не форвардится после flap
  3) `--force-recreate nginx` на каждый start
  4) API bootstrap без retry Postgres; грязный WSL restart
- fix: `ragApi` без self-wrapper; publish `${RAG_UI_PORT:-18080}:8080`; убран force-recreate; `_wait_for_db`; login retry 8×
- replay: DevTools — нет RangeError; Windows `curl http://127.0.0.1:18080/api/health` → ok; login toast «Вход выполнен»
- prevent: S41–S43, A14, A15
- links: app.js, docker-compose, launcher
- status: fixed

## 2026-11-09 — Сайт открыт, API «нет связи» / зелёный offline
- id: E-20261109-stale-wslrelay
- symptom: SPA на экране; toast «Сервер недоступен»; pill offline зелёный; WSL curl health ок
- reproduce: после WSL/dockerd flap не перезагружая вкладку
- root_cause: stale Windows `wslrelay` на :18080; pill `offline` = «без CDN», не статус API
- fix: `.wslconfig` `networkingMode=mirrored`; `scripts/fix-wsl-localhost.ps1`; launcher fallback WSL eth0; pill «локально» + «API ок/нет»
- replay: после fix — `http://127.0.0.1:18080` health 200; индикатор «API ок»
- prevent: S44, S45, A15
- links: `scripts/fix-wsl-localhost.ps1`
- status: fixed

## 2026-11-10 — Toast «нет связи» при наборе / «Отправить» в чате
- id: E-20261110-chat-no-api
- symptom: UI открыт; «Отправить» → toast про нет связи с `http://127.0.0.1:18080/`; «должно быть без интернета»
- reproduce: `RAG5060 stop` / compose down при открытой SPA; или WSL reboot mid-session
- root_cause: SPA кэш жив, API мёртв (Failed to fetch); WSL memory pressure при 20GB
- fix / ops: держать стек; `.wslconfig` memory=12GB, `vmIdleTimeout=-1`, `autoMemoryReclaim=disabled`; softer toast; `OLLAMA_NO_CLOUD=1`
- replay: стек up → chat send OK; стек down → toast про **локальный** API (не интернет)
- prevent: S46, S47, S49, A16
- links: app.js chat-form
- status: mitigated (нужен `wsl --shutdown` чтобы применить 12GB; затем start + F5)

## 2026-11-10 — «Сервер запущен» (exe), но toast нет связи
- id: E-20261110-exe-not-healthy
- symptom: exe в процессах / «готово» было; UI Failed to fetch на :18080
- reproduce: открыть UI сразу после рестарта контейнеров (Up ~20s)
- root_cause: exe жив ≠ API healthy; nginx 502 пока API starting
- fix: ждать healthy (`scripts/wait-and-open.ps1`); F5 когда «API ок»; не считать running.exe достаточным
- replay: `docker compose ps` — api/nginx healthy → затем login/chat; иначе ждать
- prevent: S39, S45, A14; ops checklist
- links: `scripts/wait-and-open.ps1`, `scripts/wait-healthy-login.sh`
- status: recurring / ops

## 2026-11-10 — F5 → ERR_CONNECTION_REFUSED на :18080
- id: E-20261110-f5-wslrelay-flap
- symptom: F5 в Chrome → «Не удается получить доступ» / `ERR_CONNECTION_REFUSED` на `127.0.0.1:18080`
- reproduce: стек «только что ок»; через 10–30 с Windows curl :18080 → refused/timeout; внутри WSL nginx listen + health ок
- root_cause: F5 не убивает сайт — умирает Windows↔WSL publish (mirrored/wslrelay). Прямой docker-bind `:18080` на Windows нестабилен
- fix: docker publish **`:28080`**; `RAG5060.exe` держит **нативный** Windows reverse-proxy на **`:18080`** → WSL IP:28080; окно exe не закрывать
- replay: 12× подряд `curl http://127.0.0.1:18080/api/health` → 200; F5 в браузере на proxy URL не даёт refused
- prevent: S52; держать `dist\RAG5060.exe start`; `scripts/fix-wsl-localhost.ps1` при полном WSL death
- links: `apps/launcher/proxy.go`, `docker-compose.yml` `RAG_DOCKER_UI_PORT`, `scripts/fix-wsl-localhost.ps1`
- status: mitigated

## 2026-11-11 — refers_to: части названий и строчные («смерти», «накануне»)
- id: E-20261111-refers-partial-lower
- symptom: top refers — «смерти/дне/шесть/накануне/шинель» как ложные ссылки на произведения
- reproduce: corpus report `artifacts/20261110-133444-graph-index/` top phrases; unit: «Пьер накануне…», «до смерти Бибикова»
- root_cause: индекс по significant words + stem first/last; lowercase и части title принимались; genre marker anywhere in chunk
- fix: `graph_refers.py` — полное title-token совпадение; только заглавная/точные кавычки; genre в 0–2 токенах; one-word только quoted|genre; строгий `_token_match` (Иван≠Иванов)
- replay: `pytest tests/unit/test_graph_refers.py`; report top-30 без строчных обломков; sample precision ≥80%
- prevent: S55, A20; язык в `configs/graph_lexicon.json`
- links: `apps/api/services/graph_refers.py`, `artifacts/20261111-050958-graph-index/`
- status: fixed

## 2026-11-11 — lex_similar: имена (Аркадий) связывают чужие тексты
- id: E-20261111-lex-proper-names
- symptom: «Слабое сердце ↔ Отцы и дети» ~0.63 только из-за Аркадия/Аркаши
- reproduce: regression bad pair в graph report; shared name stems in provenance
- root_cause: стемы имён до детекции proper; mid-cap не учитывался
- fix: mid-cap≥0.7 на словоформе до stem; diminutives/editorial/speech из lexicon; ≥3 shared; single-term >50% → drop
- replay: `pytest tests/unit/test_graph_lex_ents.py::test_slaboe_serdtse_ottsy_not_linked_by_arkady`; bad pairs absent in report
- prevent: S55, A20
- links: `graph_index.layer_d_lex_similar`, `configs/graph_lexicon.json`
- status: fixed

## 2026-11-11 — graph report: doc_cache не переживает прогон
- id: E-20261111-report-dataroot
- symptom: каждый `graph_index_report.py --force` — `cached=0`, built_ms ~100s+
- reproduce: `scripts/_run-1d-report.sh` со старым mount `/ragdata` при `DATA_ROOT=/var/lib/rag` из `.env`
- root_cause: ephemeral FS контейнера; volume не совпадал с DATA_ROOT
- fix: `-v rag5060_ragdata:/var/lib/rag` + `DATA_ROOT=/var/lib/rag`; reuse artifacts по content_hash даже при full edge rebuild
- replay: второй прогон report → `lifecycle.cached=702`, warm ~60s
- prevent: S54; не монтировать пустой host path как DATA_ROOT
- links: `scripts/_run-1d-report.sh`
- status: fixed

## 2026-11-11 — параллельная сборка / мёртвый in-memory lock
- id: E-20261111-graph-build-lock
- symptom: два rebuild подряд или после crash API — гонка/залипший «running»
- reproduce: два `POST /api/graph/index/rebuild`; или kill процесса на mid-build
- root_cause: single-flight только в памяти процесса
- fix: lock-файл `data_root()/graph/locks/<tenant>.lock` + timeout/dead-pid; atomic publish + `current/<tenant>.json`; fail не двигает pointer
- replay: `pytest tests/unit/test_graph_index_lifecycle.py::test_file_lock_single_flight_and_dead_clear` + `test_atomic_publish_keeps_previous_on_failed_pointer`
- prevent: S54, A19; `GRAPH_AUTO_REBUILD` по умолчанию false
- links: `graph_index_cache.py`, CONNECTIVITY_LIFECYCLE.md
- status: fixed

## 2026-11-11 — EN lex: Latin tokens всегда drop
- id: E-20261111-lex-latin-drop-en
- symptom: два английских tech-дока про одну технологию не получают `lex_similar`
- reproduce: `pytest tests/unit/test_graph_multilang.py::test_en_stopwords_no_spurious_lex_theme_pair_links` до фикса; `is_lex_proper_token` на `encrypt` → True
- root_cause: фильтр `re.search(r"[a-z]")` заточен под RU-корпус (латиница = шум)
- fix: `is_lex_proper_token(..., lang=)`; при `lang=en` — только EN stops/style/speech; `layer_d_lex_similar` передаёт `lang` из per-lang wrapper
- replay: `pytest tests/unit/test_graph_multilang.py` (8 passed)
- prevent: S56, A21; не применять RU proper-name pipeline к EN без `lang`
- links: `graph_index.is_lex_proper_token`, `layer_d_lex_similar`
- status: fixed

## 2026-11-11 — identifiers: римские цифры и URL как shared_entity
- id: E-20261111-id-roman-url
- symptom: в отчёте tech examples = XIV/XIX; ложные cross_lang по `/www.rvb.ru/...`
- reproduce: report `artifacts/20261111-071146-graph-index/` technical entity examples
- root_cause: `_ACRONYM` ловил Roman chapter marks; `_PATH` — любые `/a/b` URL
- fix: skip `_ROMAN`; path только unix roots (`/usr|/var|…`); tech acronym allowlist; artifact `index_version` invalidate (INDEX_VERSION=14)
- replay: `pytest tests/unit/test_graph_multilang.py::test_identifiers_skip_roman_and_web_paths`; report `20261111-072215` без XIV/OCR noise
- prevent: S56
- links: `graph_lang.extract_identifiers`
- status: fixed

## 2026-11-12 — темы = папки автора / стилевые подписи
- id: E-20261112-topics-author-folder
- symptom: `lex_same_author_share≈0.98`, mean author purity≈0.91, theme 100% одного автора; подписи «нешто, дескать, схвативши»
- reproduce: report `artifacts/20261111-104731-topics/` (slice 2 до 2b)
- root_cause: кластеризация по сырому `work_lex_similar` ловит идиолект/эпоху; c‑TF‑IDF без thematic filter
- fix: `topic_vectors` (entropy, style stops, gerunds, noun boost, author debias α=0.6) + kNN `topic_knn`, `same_author_factor=0.35`; labels только thematic dict + LLM fallback; expectations в `configs/graph_topics_expectations.json`
- replay: `pytest tests/unit/test_graph_topic_vectors.py tests/unit/test_graph_topics.py` (22); `bash scripts/_run_topics2b_report.sh` → `artifacts/20261112-052018-topics/` (theme purity 0.37, lex_share 0.35, expectations 3/3)
- prevent: S58, A23; не кластеризовать темы на `work_lex_similar`; не хардкодить авторов в коде
- links: `graph_topic_vectors.py`, `configs/graph_topics.json`, `graph_topics_report.py`
- status: fixed

## 2026-11-12 — topics report висит / O(n²) debias / двойной rebuild
- id: E-20261112-topics-report-hang
- symptom: `graph_topics_report` после `graph_index_noop` молчит минутами; CPU 100% или ждёт Ollama
- reproduce: `build_index_for_tenant` в отчёте → `_with_topics` + leave‑one‑out mean O(n²·vocab) на Чехове; LLM timeout 45s×N
- root_cause: noop index всегда зовёт topics; наивный leave‑one‑out; отчёт грузил index через full build path
- fix: author_debias через pre‑sum; term_cap/top_k; отчёт читает pointer/`read_index`; LLM timeout 90s + warm; incremental reuse `work_edges` без re‑decorate
- replay: report timings noop≈14ms, incremental≈21ms; cluster probe `artifacts/_topics2b_fast.log`
- prevent: S58; в отчётах/smoke не вызывать `build_index_for_tenant` ради topics
- links: `graph_topic_vectors.author_debias`, `graph_topics_report.py`, `graph_index._with_topics`
- status: fixed

## 2026-11-12 — имена героев в терминах тематик
- id: E-20261112-topics-proper-nouns
- symptom: top‑термины «хлестаков, нина, раскольников, федя»; кластеры по персонажам
- reproduce: report gen11 / early 2c — mid‑cap ratio занижен из‑за dialogue sent_start≫mid
- root_cause: `cap_ratio=mid/(mid+start+lower)` пропускает имена в репликах; entity ban слишком широкий/не stemmed
- fix: dialogue heuristic (upperish≥0.9, mid≥min_n, almost no lower); entity → person/place + stem; proper вес 0 в vectors/labels
- replay: `pytest …::test_proper_noun_*`; report `artifacts/20261112-064335-topics/` no_proper_in_top6
- prevent: S59; не банить нарицательные (казаки/батареи/учения/станица)
- links: `graph_topic_vectors.detect_proper_nouns`, `graph_topics_quality.entity_stems_from_index`
- status: fixed

## 2026-11-12 — recluster/labels ~100s при llm=false
- id: E-20261112-topics-labels-slow
- symptom: recluster_demo ~100s даже с `llm=False` и cache hit vectors
- reproduce: `_decorate` → `_thematic_label_terms` заново считает entropy+weight на всех bags (~94s)
- root_cause: label path повторял полный thematic filter вместо reuse `vector_debug.auto_style` + proper set
- fix: быстрый label filter (proper/style/gerund, top_k bag); vec_cache по filter_config_hash; stage_ms
- replay: recluster ~3s, cached_full ~2s в `artifacts/20261112-064335-topics/`
- prevent: S59; не звать полный entropy в decorate
- links: `graph_topics._thematic_label_terms`, `graph_topics_quality.vec_cache`
- status: fixed
