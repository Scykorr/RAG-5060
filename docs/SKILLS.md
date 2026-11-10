# Навыки (skills) — системный контекст

Канон: `docs/SKILLS.md`. Зеркало: `Обучение/docs/SKILLS.md` (вести обе копии по AGENTS.md).

## Зачем файл

Навык — **правило поведения**, которое агент/система обязана применить при триггере.  
Без колонки «Как применить» навык считается черновиком и **нельзя** ссылаться на него как на готовый.

## Контракт записи

| Поле | Смысл |
| --- | --- |
| ID | Стабильный ключ `Snn` |
| Навык | Что делать (норма) |
| Когда | Триггер / контекст |
| Как применить | 1–3 шага, путь в коде, UI или команда — **алгоритм**, не лозунг |
| Доказательство | Тест, артефакт, ERRORS/RESULTS, где видно что навык работает |

**Ритуал:** перед задачей — прочитать релевантные S*; после фикса — обновить «Как применить», если алгоритм изменился; синхронизировать зеркало.

---

| ID | Навык | Когда | Как применить | Доказательство |
| --- | --- | --- | --- | --- |
| S01 | Нормализация кириллицы (`ё→е`, lower, пробелы) | RU extractive / contain | Вызвать `norm_ru` (или эквивалент) на query и gold до сравнения | Тест 1/2 |
| S02 | Индексировать контекст вопроса отдельным TXT | Эталон — span из context | Положить TXT с заголовком/id в inbox → import; не только вопрос | XQuAD / SberQuAD |
| S03 | Extractive prompt: короткая цитата | Числовые/именные ответы | `chat_prompt_mode=extractive`; в prompt — «ответь цитатой из источников» | XQuAD |
| S04 | RuCoS: `@placeholder` → «какое слово вместо пропуска» | Cloze / entity fill | Сформировать query с `@…`; маршрут cloze в chat | Тест 3 |
| S05 | Fail: сначала retrieval, потом generation | Любой fail ответа | Смотреть chunks/prefix в ERRORS; чинить retrieve до LLM | ERRORS.md |
| S06 | Не смешивать обучающий inbox с продовым | Смена корпуса | Разные папки/`obuchenie_testN` vs `classic_ru`; при смене — S21 | harness |
| S07 | Фиксировать sample seed + revision артефактов | Любой eval | Писать seed/rev в `artifacts/<run-id>/` | artifacts/ |
| S08 | Pass/fail порог явно | Eval | Метрика + threshold в `Обучение/docs/RESULTS.md` | RESULTS.md |
| S09 | Deterministic entity fill до LLM | Cloze с эталоном-сущностью | Hard-match entity по chunks; LLM только fallback | Тест 3 |
| S10 | Denylist мета-токенов в entity extract | Enriched training docs | Исключить `документ`, `вопрос`, … из кандидатов | Тест 3 |
| S11 | Filename prefix при ревизии inbox | Изоляция пакета | Имена `classic_*` / `wildlife_*` / `t3r2_*` | harness |
| S12 | `--no-cache` rebuild API после правок chat | Docker CACHED слой | `docker compose build --no-cache api` (или `scripts/rebuild-api.sh`) | docker |
| S13 | Cloze: предложение после `Вопрос:` / `Заполни пропуск:` | Harness RuCoS | Парсер blank берёт строку после маркера | Тест 3 |
| S14 | Entity regex без `\\b` на кириллице | RU NER-lite | Unicode-aware границы, не ASCII `\b` | Тест 3 |
| S15 | Cloze hard-match → все chunks документа | Entity fill без passage | Retrieve full-doc chunks, не только tail | Тест 3 |
| S16 | Разделять body / `@highlight`; highlights слабо | Highlights оффтоп | Ranker: body ≫ highlight weight | Тест 3 |
| S17 | Hints person/geo/org по слотам blank | RuCoS slot typing | Классифицировать blank → фильтр кандидатов | Тест 3 |
| S18 | Quoted boost не ломает person-fill | «Красава» vs ФИО | Boost кавычек только если слот не person | Тест 3 |
| S19 | Offline dry-run cloze до live API | Дешёвая итерация | `debug_cloze.py` на inbox TXT | debug_cloze |
| S20 | WSL: пути с пробелами в кавычках | Shell | Wrapper/`"D:/RAG 5060/..."` | harness |
| S21 | Перед сменой тем: полный reset | Смена/смешение корпусов | UI «Очистить всё…» или `POST …/reset-rag?confirm=true` | UI / admin |
| S22 | Классика: `prefix=classic_` + extractive | Только литература | Chat-eval-config: prefix + `chat_prompt_mode=extractive` | classic_ru |
| S23 | Читать SKILLS/ERRORS/ABILITIES (+ зеркало) | Старт задачи агента | Открыть хвост трёх файлов в `docs/` и `Обучение/docs/` | AGENTS |
| S24 | Корпус ≥500: phrase/term-prefilter | Classic QA | SQL lexical prefilter до LIMIT; не слепой top-N | classic QA |
| S25 | Classic gold: unique 5-gram в «кавычках» | Lexical hit на 500+ | Needle уникален в корпусе; вопрос цитирует needle | attempt19 |
| S26 | Cloze blank: парсить и `Заполни пропуск:` | Wrong q_core | Parser в `chat.py` принимает оба маркера | chat.py |
| S27 | Мультитематика: подпапки + auto prefix | Иначе смешение понятий | Inbox subdirs; `corpus_packs` штампует prefix | corpus_packs |
| S28 | `reset-rag` не трогает skills/errors/abilities | Очистка индекса | Проверить после reset: `docs/*` на месте | admin.py |
| S29 | Python deps только из vendor wheels | Офлайн runtime | Раз: `scripts/vendor-python-wheels.sh`; Dockerfile `--no-index` | Dockerfile |
| S30 | Deny-list `_manifest` / служебных файлов | Чистый корпус | Tombstone/deny при import | N6 |
| S31 | Preview → UI выбор pack → confirm import | Импорт папки | Не писать в индекс до кнопки «Загрузить» | corpus_packs + UI |
| S32 | `import-folder/preview` до import | API import | Preview → затем import с `pack_mode`/`pack_id` | documents.py |
| S33 | Intent: catalog / overview / summarize / extractive / cloze | Произвольные RU вопросы | `query_intent.py` → ветка в `chat.py` | query_intent |
| S34 | Catalog «сколько/названия» из имён файлов | Точный счётчик | Без LLM; aggregate filename author | chat catalog |
| S35 | Граф: PG edges + Cytoscape vendor; ACL=tenant | Вкладка «Граф» | `/api/graph*`; UI tab; offline vendor | docs/graph/ |
| S36 | Нулевые Qdrant vectors → lexical + DEGRADED | Graph similarity | `token_jaccard_v1`; не выдавать fake cosine | graph detectors |
| S37 | UI: `http://127.0.0.1:18080` | Логин / лаунчер | `RAG_UI_PORT`; не `:8080` | compose + launcher |
| S38 | Кнопка «Войти» никогда `disabled` | Вход не молчит | Всегда clickable + toast + API status | `apps/web/app.js` |
| S39 | Лаунчер: 3× JSON `status=ok` до браузера | Старт стека | Не ждать Ollama healthy для API ready | launcher |
| S40 | Не rehash bcrypt admin каждый рестарт | Bootstrap | Только drift / first boot / смена в UI | bootstrap.py |
| S41 | Не `window.api = (...a) => api(...a)` | Login / любой api() | `window.api = ragApi` (прямая ссылка) | `app.js` |
| S42 | Publish UI-порта без `127.0.0.1:` в WSL2 | Доступ с Windows | `"${RAG_UI_PORT:-18080}:8080"` | docker-compose |
| S43 | Лаунчер: не `--force-recreate` на каждый start | Порт mid-login | Обычный `up -d`; recreate только вручную | launcher |
| S44 | WSL mirrored + fix stale relay | «Сайт есть / API нет» | `.wslconfig` mirrored; `scripts/fix-wsl-localhost.ps1` | WSL |
| S45 | Pill «локально» ≠ API alive | Диагностика UI | Смотреть индикатор «API ок/нет» | index/app.js |
| S46 | Runtime полностью offline | Чат/login/graph | Compose internal; `OLLAMA_NO_CLOUD=1`; без CDN | compose |
| S47 | Chat send = `POST /api/chat` same origin | Жалоба «нет интернета» | Проверить стек/`RAG5060 start`; Failed to fetch ≠ cloud | app.js |
| S48 | Читать/обновлять обе копии + ROADMAP/progress | Правки качества/логина/чата/WSL | Канон `docs/` + зеркало `Обучение/docs/` | AGENTS |
| S49 | Chat send: health-probe + 1 retry; «Думаю…» | UX отправки | Toast про локальный API, не интернет | app.js |
| S50 | Graph UI: layout/hover/pulse offline | Вкладка графа | `graph.js` + styles; без CDN | graph.js |
| S51 | Знания: skill/ability/error только с apply/replay | Любая новая запись в трёх файлах | Заполнить контракт файла; иначе статус draft | этот файл |
| S52 | UI через Windows-прокси :18080 → docker :28080 | F5 / ERR_CONNECTION_REFUSED | Держать `RAG5060.exe start` (не закрывать окно); docker publish 28080 | launcher/proxy.go |
| S53 | Graph Obsidian-like на Cytoscape (не D3) | Вкладка графа / «сетка» узлов | Hide orphans; size∝degree; BFS depth; click→local; Анализ при 0 рёбер | graph.js |
| S54 | Connectivity index offline + lifecycle | Сборка графа / reset / дозагрузка корпуса | Только локальный корпус; кэш tenant+fp+version+params_hash; per-doc artifacts; full|incremental; invalidate на reset-rag; пустой корпус → пустой индекс; фон + single-flight | docs/graph/CONNECTIVITY_LIFECYCLE.md; graph_index.py |

---

## Быстрый индекс по теме

| Тема | Skills |
| --- | --- |
| Retrieval / classic QA | S01, S03, S05, S22, S24, S25 |
| Cloze / RuCoS | S04, S09–S19, S26 |
| Корпус / prefix / reset | S06, S11, S21, S27, S28, S30–S32, S54 |
| Intent / catalog | S33, S34 |
| Login / WSL / UI port | S37–S45, S47, S49, S52 |
| Graph Explorer | S50, S53, S54 |
| Offline / deps | S29, S46, S54 |
| Graph | S35, S36, S50, S54 |
| Ритуал агента | S23, S48, S51 |
