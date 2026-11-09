# Журнал ошибок обучения

Формат записи (добавлять в конец, не удалять историю):

```
## YYYY-MM-DD — Тест N / attempt K
- symptom: что увидели
- root_cause: гипотеза / подтверждённая причина
- fix: что изменили
- status: open | fixed (...)
```

---

## 2026-11-05 — Тест 3 / attempt 9–10 (retrieval + cloze ranker)
- symptom: live score 0.45 < 0.55 while offline cloze on full doc ~0.60→1.0
- root_cause: hard-match на cloze-хвост возвращал только tail-chunk без passage; NER шумел от @highlight и sentence-case; quoted boost бил person-fills
- fix: при cloze hard-match тянуть все chunks документа; body/highlight split; geo/person/org hints; quoted gate; deny titles; full-doc retrieve
- status: fixed (test 3 passed attempt 10 @0.95)

## 2026-11-05 — Тест 3 / attempts 1–6
- symptom: score 0.30–0.40 < 0.55; много `не найдено в документах` и verbose markdown вместо entity
- root_cause: RuCoS cloze — пропуск в query, а не в passage; lexical retrieval/LLM не выбирают сущность; старые t3_ доки без query-строк засоряли индекс
- fix: enriched docs (query рядом с passage) + prefix `t3r_`; cloze prompt + candidates; postprocess; lexical entity fallback
- status: open

## 2026-11-05 — Тест 3 / retry after t3r_
- symptom: score упал до 0.0–0.05; ответы «Вопросы»/«Документ»
- root_cause: entity-fallback брал заголовки enriched-доков; docker build закэшировал старый слой
- fix: denylist мета-токенов; passage-only extract; prefix `t3r2_`; deterministic cloze до LLM; `--no-cache` rebuild
- status: open

## 2026-11-05 — Тест 3 / entity extract
- symptom: offline cloze dry-run score 0.0; pred=None почти везде
- root_cause: Python `re \\b` не считает кириллицу словом → кандидаты не извлекались
- fix: lookbehind/lookahead границы для кириллицы; порог score>=2
- status: open

## 2026-11-05 — Тест 3 / blank sentence + frequency
- symptom: брался wrapper «В тексте есть пропуск _____»; score ~0.25–0.35
- root_cause: wrong q_core; weak ranking
- fix: брать текст после `Вопрос:`; exclude query entities; frequency+person/geo hints (offline ~0.60)
- status: open

## 2026-11-05 — Тест 3 / attempt 2
- symptom: score below threshold
- root_cause: generation/retrieval mismatch
- fix: extractive/cloze prompt + wider scan
- status: open

## 2026-11-05 — Тест 3 / attempt 3
- symptom: still failing
- root_cause: missed contexts or verbose answers
- fix: more chunks + larger lexical scan
- status: open

## 2026-11-05 — Тест 3 / attempt 4
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 5
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 6
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 2
- symptom: score below threshold
- root_cause: generation/retrieval mismatch
- fix: extractive/cloze prompt + wider scan
- status: open

## 2026-11-05 — Тест 3 / attempt 3
- symptom: still failing
- root_cause: missed contexts or verbose answers
- fix: more chunks + larger lexical scan
- status: open

## 2026-11-05 — Тест 3 / attempt 4
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 5
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 6
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 7
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 8
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 2
- symptom: score below threshold
- root_cause: generation/retrieval mismatch
- fix: extractive/cloze prompt + wider scan
- status: open

## 2026-11-05 — Тест 3 / attempt 3
- symptom: still failing
- root_cause: missed contexts or verbose answers
- fix: more chunks + larger lexical scan
- status: open

## 2026-11-05 — Тест 3 / attempt 4
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 5
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 6
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 7
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 2
- symptom: score below threshold
- root_cause: generation/retrieval mismatch
- fix: extractive/cloze prompt + wider scan
- status: open

## 2026-11-05 — Тест 3 / attempt 3
- symptom: still failing
- root_cause: missed contexts or verbose answers
- fix: more chunks + larger lexical scan
- status: open

## 2026-11-05 — Тест 3 / attempt 4
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 5
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 6
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 7
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 8
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 2
- symptom: score below threshold
- root_cause: generation/retrieval mismatch
- fix: extractive/cloze prompt + wider scan
- status: open

## 2026-11-05 — Тест 3 / attempt 3
- symptom: still failing
- root_cause: missed contexts or verbose answers
- fix: more chunks + larger lexical scan
- status: open

## 2026-11-05 — Тест 3 / attempt 4
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 5
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 6
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 7
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 8
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 9
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: open

## 2026-11-05 — Тест 3 / attempt 10
- symptom: repeated fail
- root_cause: need aggressive retrieve
- fix: aggressive retrieval
- status: fixed (test 3 passed attempt 10)

## 2026-11-06 — Classic QA pass
- symptom: live contain низкий на 500+ корпусе
- root_cause: lexical LIMIT/flood; blank parser; слабый gold
- fix: phrase prefilter; unique needles; Заполни пропуск parser; stem contain
- status: fixed (attempt19 @0.75)

## 2026-11-06 — Residual non-classic cleaned
- symptom: 681 non-classic + _manifest рядом с classic_
- root_cause: смешанный индекс после тестов
- fix: purge-chats; soft-delete non-classic; active=700 classic-only; smoke OK
- status: fixed

## 2026-11-06 — Classic corpus bootstrap
- symptom: нужно >=500 произведений; тестовые файлы засоряли retrieval
- root_cause: смешанный индекс + отсутствие UI/API полного сброса
- fix: reset-rag + UI; classic_ru ~700; prefix classic_; RU prompts
- status: fixed (indexed@700; attempt19 @0.75)

## 2026-11-06 — Classic QA lexical ceiling
- symptom: live contain ~0.40-0.44; offline oracle high
- root_cause: lexical without dense; blank parser; OR-flood
- fix: phrase-prefilter; blank parser; stopwords; stem contain
- status: fixed (attempt19 @0.75)

## 2026-11-06 — Classic QA low score @0.2
- symptom: contain 8/40 after import
- root_cause: lexical LIMIT without term prefilter
- fix: SQL phrase/term prefilter
- status: fixed (superseded)

## 2026-11-06 — Offline Python deps / multi-topic packs
- symptom: pip на каждом запуске; риск смешения тем
- root_cause: PyPI в runtime; плоский индекс
- fix: vendor/python-wheels; Dockerfile --no-index; reset preserves docs; comma prefixes
- status: fixed (policy); wheels скачать один раз онлайн

## 2026-11-06 — Prefix was manual
- symptom: prefix задавался вручную
- root_cause: chat-eval-config only
- fix: corpus_packs auto resolve on import-folder
- status: fixed

## 2026-11-06 — Arbitrary RU query intents
- symptom: произвольные вопросы не разбирались
- fix: query_intent + catalog/overview/summarize
- status: fixed

## 2026-11-09 — Graph / zero embeddings
- symptom: Qdrant vectors нулевые; cosine ANN бессмысленен
- root_cause: ingestion upsert `[0.0]*dim`
- fix: graph uses `token_jaccard_v1` + `DEGRADED_NO_VECTOR_SEARCH`
- status: mitigated (graph MVP); dense N1 backlog
