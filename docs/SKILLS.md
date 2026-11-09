# Навыки (skills) — системный контекст

Канон: `docs/SKILLS.md`. Зеркало: `Обучение/docs/SKILLS.md` (вести обе копии по AGENTS.md).

| ID | Навык | Когда применять | Доказательство |
| --- | --- | --- | --- |
| S01 | Нормализация кириллицы (`ё→е`, lower, пробелы) при скоринге и retrieval | Русский extractive QA | Тест 1/2 |
| S02 | Индексировать **контекст вопроса** как отдельный TXT с заголовком/id | Эталон — span из context | XQuAD / SberQuAD |
| S03 | Extractive prompt: «ответь короткой цитатой из источников» | Числовые и именные ответы | XQuAD |
| S04 | RuCoS: вопрос = query с `@placeholder` → «какое слово/имя вместо пропуска» | Cloze / entity fill | Тест 3 |
| S05 | При fail: сначала retrieval, потом generation | Любой fail | ERRORS.md |
| S06 | Не смешивать обучающий inbox с продовым: `obuchenie_testN` vs `classic_ru` | Смена корпуса | harness |
| S07 | Фиксировать sample seed и revision артефактов | Сравнимость попыток | artifacts/ |
| S08 | Pass/fail порог и метрика явно в RESULTS.md | Не «зелёный на глаз» | RESULTS.md |
| S09 | RuCoS: deterministic entity fill до LLM | Cloze с эталоном-сущностью | Тест 3 |
| S10 | Denylist мета-токенов (`документ`, `вопрос`, …) в entity extract | Enriched training docs | Тест 3 |
| S11 | Filename prefix при ревизии inbox (`classic_`, `wildlife_`, `t3r2_`) | Изоляция пакета | harness |
| S12 | `--no-cache` rebuild API после правок chat | Docker CACHED слой | docker |
| S13 | Cloze: брать предложение после `Вопрос:` / `Заполни пропуск:` | Harness RuCoS | Тест 3 |
| S14 | Entity regex: не использовать `\\b` на кириллице | RU NER-lite | Тест 3 |
| S15 | Cloze hard-match → все chunks документа, не только tail | Entity fill без passage | Тест 3 |
| S16 | Разделять body / `@highlight`; highlights слабо весить | Highlights оффтоп | Тест 3 |
| S17 | Hints: person / geo / org по слотам blank | RuCoS slot typing | Тест 3 |
| S18 | Quoted boost только если не ломает person-fill | «Красава» vs ФИО | Тест 3 |
| S19 | Offline dry-run cloze на inbox TXT до live API | Дешёвая итерация | `debug_cloze.py` |
| S20 | WSL: пути с пробелами — wrapper/кавычки | Shell quoting | harness |
| S21 | Перед сменой/смешением тем: UI «Очистить всё хранилище и чаты» → `reset-rag` | Изоляция корпусов | UI |
| S22 | Классика: `prefix=classic_` + `chat_prompt_mode=extractive` | Только литература | classic_ru |
| S23 | Читать `docs/{SKILLS,ERRORS,ABILITIES}` (+ зеркало `Обучение/docs/`) перед задачами | Контекст агента = системы | AGENTS |
| S24 | Корпус ≥500: lexical SQL phrase/term-prefilter, не слепой LIMIT | Иначе recall≈random | classic QA |
| S25 | Classic gold: corpus-unique 5-gram needle в «кавычках» | Lexical hit на 500+ | attempt19 |
| S26 | Cloze blank: парсить и `Заполни пропуск:` | Иначе wrong q_core | chat.py |
| S27 | Мультитематика: подпапки inbox; prefix ставит система сама (`classic_`, `wildlife_`, …) | Иначе понятия смешиваются | corpus_packs |
| S28 | `reset-rag` чистит чаты/индекс/scratch/Qdrant; **не** трогает skills/errors/abilities | Знания системы остаются | admin.py |
| S29 | Python deps только из `vendor/python-wheels` (`scripts/vendor-python-wheels.sh` один раз онлайн) | Офлайн runtime | Dockerfile |
| S30 | Служебные `_manifest`/служебные файлы — deny-list / tombstone | Чистый корпус | N6 |
| S31 | Авто-категория: preview → UI выбор existing/new → confirm import | Пользователь жмёт итоговую кнопку | corpus_packs + UI |
| S32 | `POST /documents/import-folder/preview` до загрузки; import только с `pack_mode`/`pack_id` | Не писать в индекс без выбора | documents.py |
| S33 | Разбор intent запроса: catalog / author_overview / summarize / extractive / cloze | Произвольные RU вопросы | query_intent.py |
| S34 | Catalog «сколько/названия Чехова» — из имён файлов, без LLM | Точный счётчик корпуса | chat catalog |
| S35 | Граф связей: только PostgreSQL edges + Cytoscape vendor; ACL = tenant как у documents | Вкладка «Граф связей» | `/api/graph`, `docs/graph/` |
| S36 | Пока Qdrant vectors нулевые — `token_jaccard_v1` + флаг `DEGRADED_NO_VECTOR_SEARCH`; не выдавать cosine | Честная доказательность | graph detectors |
