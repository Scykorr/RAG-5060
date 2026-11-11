# Умения / способности системы (abilities)

Канон: `docs/ABILITIES.md`. Зеркало: `Обучение/docs/ABILITIES.md` (+ `RESULTS.md` прогонов).

## Зачем файл

Умение — **заявленная способность**, которую можно **включить и проверить**.  
Строка «умеем X» без «как применить» и без **replay** — не умение, а пожелание.

## Контракт записи

| Поле | Смысл |
| --- | --- |
| ID | `Ann` |
| Умение | Что система умеет для пользователя/агента |
| Уровень | `yes` / `pass@…` / `indexed@…` / `degraded` / `no` |
| Как применить | Как вызвать: UI путь, API, конфиг, prefix |
| Replay | Команда/скрипт/smoke, который **повторяет** проверку умения |
| Skills | Какие S* реализуют умение |

**Правила:**

1. Не ставить `yes`/`pass` без рабочего Replay (скрипт или явные шаги + ожидаемый выход).
2. Replay fail → понизить уровень (`degraded`/`no`) и завести/обновить ERRORS.
3. После фикса обновить Replay, если путь проверки изменился.
4. Синхронизировать зеркало `Обучение/docs/ABILITIES.md`.

---

| ID | Умение | Уровень | Как применить | Replay | Skills |
| --- | --- | --- | --- | --- | --- |
| A00 | Локальный chat + история + диспетчер offline | baseline | UI `:18080` → Войти `admin`/`admin` → чат | `scripts/verify-login-e2e.sh`; отправить сообщение в UI | S37–S39, S46 |
| A01 | Extractive QA (XQuAD-style) | pass@0.7 | Корпус тест1; `chat_prompt_mode=extractive` | `Обучение` harness Тест 1 → RESULTS | S01–S03 |
| A02 | Extractive QA SberQuAD | pass@0.9 | Корпус тест2; extractive | Harness Тест 2 → RESULTS | S01–S03 |
| A03 | Cloze / entity fill (RuCoS) | pass@0.95 | RuCoS inbox + cloze route | Harness Тест 3 / attempt10; offline `debug_cloze` | S04, S09–S19, S26 |
| A04 | Импорт папки inbox offline | yes | UI Документы → import-folder (после wheels) | `scripts/smoke-folder-import.sh` | S29, S31, S32 |
| A05 | Ответ на языке вопроса (RU) | yes | RU вопрос в чате | Smoke RU chat; ответ на русском | S03, S33 |
| A06 | Полная очистка хранилища и чатов | yes | UI «Очистить всё…» или `POST /api/admin/data/reset-rag?confirm=true` | После reset: docs пусты в UI; `docs/SKILLS\|ERRORS\|ABILITIES` на диске целы | S21, S28 |
| A07 | Корпус русской классики ≥500 | indexed@700 | Inbox `classic_ru` → import; prefix `classic_` | Counts в Storage / `/api/documents`; classic≥500 | S11, S22, S27 |
| A08 | Classic RU QA (unique-needle) | pass@0.75 | prefix `classic_`; gold с unique 5-gram | Quality/eval attempt19 thr=0.55; oracle 18/20 | S24, S25 |
| A09 | Мультипакетный retrieval | yes | `retrieval_filename_prefix=classic_,wildlife_` | Chat с двумя темами; хиты из обоих prefix | S27 |
| A10 | Авто-категория/префикс при import | yes | import-folder → resolve_pack | Preview показывает pack; файл штампуется prefix | S31, S32 |
| A11 | UI: выбор existing/new pack + «Загрузить» | yes | Документы: dropdown/new → Загрузить | Нет автозаписи до confirm | S31 |
| A12 | Произвольный RU-запрос (catalog/overview/summarize) | yes | Вопрос в чате; смотреть `intent` в API | «сколько Чехова…» → catalog + список; см. AGENTS | S33, S34 |
| A13 | Graph Explorer + evidence | yes | Вкладка «Граф связей» | `scripts/smoke-graph.sh` / `smoke-graph-evidence.sh` | S35, S36 |
| A14 | Стабильный локальный вход через лаунчер | yes | `RAG5060 start` → браузер `:18080` → Войти | `scripts/verify-login-e2e.sh` → `LOGIN_E2E_OK` | S37–S43 |
| A15 | Вход устойчив к короткому WSL/Docker flap | yes | Войти при кратком flap | UI doLogin до 8×; API `_wait_for_db` | S38, S44, S49 |
| A16 | Offline runtime (без cloud) | yes | Compose up; чат/Ollama внутри сети | Нет внешних URL в Network; `OLLAMA_NO_CLOUD=1` | S46, S47 |
| A17 | Graph UI: анимация layout / hover / pulse | yes | Вкладка графа; hover узла | Визуально + `graph.js` offline vendor | S50 |
| A18 | Graph Obsidian-like: chain BFS, orphans, degree size | yes | Клик узла → local; hover depth≥2 dim | `/graph.js` содержит `highlightChain`; hide-orphans | S53 |
| A19 | Connectivity index: full replace + incremental top-up offline | yes | reset-rag → rebuild empty; import → stale; rebuild manual/auto; add edition/volume → work/canonical/author; delete → edges gone; fail build → previous index | `pytest tests/unit/test_graph_index_lifecycle.py` (params_hash/lexicon, lock, stale, tenant isolation, test6 edition) | S54 |
| A20 | Connectivity quality 1d/1e | yes | Rebuild → duplicates/contains, work-lex regression (Gandhi/безумие/Воскресение↔Карамазовы), bad clean, co_cited not alpha, entity strong rare, work_mentions>0, noop≤2s | `pytest tests/unit/test_graph_*.py` (84); report `artifacts/20261111-062930-graph-index/` vs `20261111-050958` (INDEX_VERSION=11→13 multilang); isolated_works 1.3%; noop≈1.9s | S54, S55 |
| A21 | Multilang + technical connectivity | yes | ru/en/mixed detect; EN lex without stop/style edges; RU↔EN via shared_entity cross_lang (RFC/TLS); Spec v1.2/v1.3 → edition_of; no patronymic on EN | `pytest tests/unit/test_graph_multilang.py` (8) + full `test_graph_*.py` (84); fixtures `tests/fixtures/graph_multilang/`; report `artifacts/20261111-072215-graph-index/` INDEX_VERSION=14 | S56 |
| A22 | Theme/topic levels (no UI) | yes | Rebuild индекса пишет topics; `GET /api/graph/overview?view=levels`; same_author_factor 0.6; half-rule author purity | `pytest tests/unit/test_graph_topics.py tests/unit/test_graph_topics_api.py` (17); report `artifacts/20261111-104731-topics/` — 19 topics, 4 themes, unthemed 1.6%, pure100 share 0.4706 | S57 |

## Контекст для смены документов (replay корпуса)

1. **Reset:** UI «Очистить всё хранилище и чаты» / `POST /api/admin/data/reset-rag` (`confirm=true`) — чаты, chunks, docs, scratch, Qdrant. **Не трогает** `docs/SKILLS|ERRORS|ABILITIES` и `Обучение/docs/*` (A06, S28).
2. **Положить** TXT в `D:\RAG5060\inbox\<pack>` (имена с prefix темы или дать системе проставить через packs).
3. **Import** через UI/API (A10/A11); выставить `retrieval_filename_prefix` = один prefix или `classic_,wildlife_` (A09).
4. **Проверить:** sample + containment после `norm_ru` (S01); при регрессии — ERRORS + понизить затронутое Ability.
5. **Обновить** обе копии SKILLS/ERRORS/ABILITIES и хвост `docs/progress.md`.
