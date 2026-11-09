# Умения / способности системы (abilities)

Канон: `docs/ABILITIES.md`. Зеркало: `Обучение/docs/ABILITIES.md` (+ `RESULTS.md` прогонов).

| ID | Умение | Уровень | Корпус/тест | Примечание |
| --- | --- | --- | --- | --- |
| A00 | Локальный chat + история + диспетчер offline | baseline | MVP UI | admin/admin |
| A01 | Extractive QA по русским абзацам (XQuAD-style) | pass@0.7 | Тест 1 | |
| A02 | Extractive QA по SberQuAD validation | pass@0.9 | Тест 2 | |
| A03 | Cloze/entity fill (RuCoS) | pass@0.95 | Тест 3 | deterministic fill + full-doc retrieve |
| A04 | Импорт папки inbox без интернета после загрузки корпуса | yes | folder import | deps offline via wheels |
| A05 | Ответ на языке вопроса (RU) | yes | chat prompt | усилен RU extractive/general |
| A06 | Очистка всего хранилища и чатов (+ UI) | yes | storage/chat | `reset-rag`; skills/errors/abilities сохраняются |
| A07 | Корпус русской классики ≥500 | indexed@700 | `inbox/classic_ru` | prefix `classic_` |
| A08 | Classic RU QA (unique-needle) | pass@0.75 | attempt19 thr=0.55 | offline oracle 18/20 |
| A09 | Мультипакетный retrieval (comma prefixes) | yes | chat retrieve | `classic_,wildlife_` |
| A10 | Авто-категория/префикс при импорте папки | yes | corpus_packs | preview + выбор пользователя |
| A11 | UI: existing dropdown / new pack + «Загрузить» | yes | documents UI | без автозаписи до confirm |
| A12 | Анализ произвольного RU-запроса (каталог/обзор/изложение) | yes | query_intent + chat | intent в ответе API; см. AGENTS.md |
| A13 | Graph Explorer: локальный/глобальный граф документов + evidence | yes | `/api/graph` + UI tab | Cytoscape offline; lexical similarity пока нет real embeddings |

## Контекст для смены документов

1. UI «Очистить всё хранилище и чаты» / `POST /api/admin/data/reset-rag` (`confirm=true`) — чаты, chunks, tombstone docs, scratch, Qdrant. **Не трогает** `docs/SKILLS|ERRORS|ABILITIES` и `Обучение/docs/*`.
2. Положить TXT в `D:\RAG5060\inbox\<pack>` с filename prefix темы (`classic_…`, `wildlife_…`).
3. Импорт через UI/API; выставить `retrieval_filename_prefix` = один prefix или `classic_,wildlife_` для concurrent тем.
4. Sample + containment после `norm_ru` (S01); обновить обе копии SKILLS/ERRORS/ABILITIES.
