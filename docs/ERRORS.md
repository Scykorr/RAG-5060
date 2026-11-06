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
