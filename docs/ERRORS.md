# Журнал ошибок (канон в docs/)

Агенты читают этот файл перед отладкой RAG. Полная история обучения: `Обучение/docs/ERRORS.md`.

Формат:

```
## YYYY-MM-DD — контекст
- symptom:
- root_cause:
- fix:
- status: open | fixed (...)
```

---

## 2026-11-06 — Offline Python deps / multi-topic packs
- symptom: `pip install` на каждом запуске harness; риск смешения тем в одном индексе
- root_cause: Dockerfile/scripts качали PyPI; один плоский индекс без prefix-политики
- fix: `vendor/python-wheels` + `scripts/vendor-python-wheels.sh`; Dockerfile `--no-index`; UI full reset сохраняет docs skills; comma prefixes `classic_,wildlife_`
- status: fixed (policy); wheels нужно один раз скачать онлайн

## 2026-11-06 — Prefix was manual
- symptom: пользователь не понимал, как задаётся prefix; риск смешения категорий
- root_cause: `retrieval_filename_prefix` только вручную в chat-eval-config
- fix: `corpus_packs.resolve_pack` при import-folder (alias/content/new); stamp `prefix+filename`; sync active prefixes
- status: fixed

## 2026-11-06 — Residual non-classic after classic import
- symptom: 681 non-classic versions рядом с 700 classic_; `_manifest.txt` в индексе
- root_cause: повторный import тестовых пакетов / не полный reset перед classic
- fix: purge-chats; soft-delete non-classic; удалить chunks; leave only classic_; N6 deny-list `_manifest`
- status: fixed (active classic-only; smoke Anna Karenina quote OK)

## 2026-11-06 — Classic corpus bootstrap
- symptom: нужно ≥500 произведений; тестовые RuCoS/XQuAD файлы засоряли retrieval
- root_cause: смешанный индекс + отсутствие UI/API полного сброса
- fix: `reset-rag` + кнопки UI; корпус `classic_ru` (~700 TXT); prefix `classic_`; RU prompts
- status: fixed (indexed classic=700; QA attempt19 @0.75)

## 2026-11-06 — Classic QA lexical ceiling
- symptom: live contain ~0.40–0.44 на unique-needle sample (n=25); offline oracle needle+answer in file = 1.0
- root_cause: lexical ILIKE без dense; cloze blank parser не видел «Заполни пропуск»; OR-flood / AND-brittleness
- fix: phrase-prefilter; blank parser; RU stopwords; stem contain; roadmap N1 dense embeddings
- status: fixed (attempt19 contain 0.75 @ threshold 0.55; offline oracle 18/20)

## 2026-11-06 — Classic QA low score @0.2
- symptom: contain 8/40 after full classic import
- root_cause: lexical retrieve LIMIT без term prefilter
- fix: SQL phrase/term prefilter
- status: fixed (superseded by unique-needle + phrase filter → ~0.44)

## 2026-11-05 — Тест 3 cloze (итог)
- symptom: live 0.45 vs offline 1.0
- root_cause: cloze-tail chunk without passage; highlight/quoted noise
- fix: full-doc retrieve + ranker; attempt10 @0.95
- status: fixed

## 2026-11-06 — Arbitrary RU query intents
- symptom: вопросы вида «о творчестве Пушкина», «сколько Чехова», «изложение рассказа» не маршрутизировались
- root_cause: один extractive/general путь без intent
- fix: query_intent (catalog/overview/summarize) + filename author catalog
- status: fixed (Chekhov catalog smoke 77 titles)

## 2026-11-09 — Graph / zero embeddings
- symptom: Qdrant points имеют нулевые векторы; cosine ANN бессмысленен
- root_cause: `ingestion._maybe_qdrant_upsert` пишет `[0.0]*dim`
- fix (graph): lexical `token_jaccard_v1` + статус `DEGRADED_NO_VECTOR_SEARCH`; не фейковать embedding cosine
- status: mitigated for graph MVP; N1 dense embeddings still backlog

## 2026-11-09 — Login silent / «нет ответа» при «готово» в exe
- symptom: «Войти» ничего не делает; или health «не готов» при сообщении лаунчера «API готов»; F5 → connection refused на :8080
- root_cause: (1) `disabled` на кнопке глотает click; (2) один flaky health 200 через WSL/proxy; (3) nginx отдаёт HTML при 502, API перезапускается; (4) конфликт/флап порта 8080
- fix: кнопка всегда кликабельна + toast; poll health на login; лаунчер — 3× JSON `status=ok`; UI порт **18080**; API `depends_on` ollama=`service_started`; skip bcrypt rehash; `scripts/verify-login-e2e.sh`
- status: fixed (E2E LOGIN_E2E_OK на :18080)

## 2026-11-09 — Launcher окно сразу закрывается
- symptom: после exe терминал мигает; браузер открыт на неготовый API
- root_cause: process exit без pause; openBrowser до стабильного health; `sudo docker` мог ломаться
- fix: pause Enter; strict health; prefer `docker` без sudo; лог `logs/launcher.log`
- status: fixed

## 2026-11-09 — Войти → stack overflow / Windows localhost dead
- symptom: «Войти» не входит (или тихо); WSL `curl :18080` ок, Windows/browser — connection refused; в Console `RangeError: Maximum call stack size exceeded`
- root_cause:
  1) Graph expose `window.api = (...a) => api(...a)` в classic script: `api` ≡ `window.api` → бесконечная рекурсия на каждый login/API вызов
  2) publish `127.0.0.1:18080` в WSL2 часто не форвардится в Windows после WSL/dockerd flap
  3) launcher `--force-recreate nginx` на каждый start ронял порт mid-login
  4) API bootstrap без retry при Postgres «starting up»; WSL unclean restart (`WaitForBootProcess` / journal corrupt)
- fix: `ragApi` + `window.api = ragApi` (без self-wrapper); порт `${RAG_UI_PORT:-18080}:8080`; убран force-recreate; `_wait_for_db`; login retry до 8×; nginx `depends_on` api healthy
- status: fixed (browser: gate hidden, toast «Вход выполнен», чаты видны)

## 2026-11-09 — Сайт открыт, API «нет связи» / зелёный offline
- symptom: SPA/граф на экране, toast «Сервер недоступен»; pill `offline` зелёный; WSL curl health ок
- root_cause: HTML уже в памяти браузера, а Windows `wslrelay` на `:18080` stale (Listen, но Connection refused); relative `/api/*` бьёт в мёртвый localhost. Pill `offline` = «без CDN», не статус API.
- fix: `.wslconfig` `networkingMode=mirrored`; `scripts/fix-wsl-localhost.ps1`; launcher fallback на WSL eth0 IP; UI pill «локально» + «API ок/нет»; heartbeat
- note: MantaRay VPN (split «кроме РФ») *может* ломать loopback/WSL, но в этом инциденте починилось **без** отключения VPN → первопричина была stale wslrelay/mirrored, не VPN
- status: fixed (127.0.0.1:18080 health 200 после mirrored restart)
