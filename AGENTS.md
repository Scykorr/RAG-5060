# Agents (development, not RAG runtime)

Use only the RTX5060Ti pair: `ROADMAP_local_RAG_RTX5060Ti_16GB.md` + `CODEX_CURSOR_PROMPT_RTX5060Ti_16GB.md`.

Перед задачами по качеству/корпусу/чату читать и обновлять **обе копии**:

- `docs/SKILLS.md`, `docs/ERRORS.md`, `docs/ABILITIES.md` — канон (агент + система)
- `Обучение/docs/{SKILLS,ERRORS,ABILITIES}.md` — зеркало (синхронизировать при правках) + `RESULTS.md` прогонов

`reset-rag` / UI «Очистить всё хранилище» чистит индекс и чаты, но **не** эти файлы знаний.

Офлайн Python: один раз онлайн `bash scripts/vendor-python-wheels.sh` → `vendor/python-wheels*`; Dockerfile/`Обучение` harness ставят пакеты `--no-index`.

## Multi-chat + Git (обязательно)

Несколько чатов Cursor работают в **одном** дереве файлов. Чужую историю чата не видно — только диск и git.

**Перед любой задачей:**

1. `git status` и `git log -5 --oneline`
2. Прочитать хвост `docs/progress.md` и при необходимости `docs/backlog.md`
3. Если working tree грязный и изменения не твои / непонятны — **остановиться** и уточнить у пользователя (не затирать чужую работу)

**После завершённой задачи (один логический срез):**

1. Кратко обновить `docs/progress.md` / backlog при смене статуса
2. Сделать **git commit** с сообщением «why» (1–2 предложения), без `.env` и тяжёлых данных
3. Не `push`, не `--force`, не `--amend` чужих коммитов без явной просьбы пользователя

Параллельные GPU/compose-тесты двух чатов на одном ПК запрещены (см. Hard rules).

## Roles

| Role | Owns | Do not |
| --- | --- | --- |
| Integrator | ADR, backlog, API contracts, final integration | Parallel full-stack GPU tests |
| Infrastructure | WSL/Engine/Toolkit/Compose/scripts | Delete volumes for green tests |
| Backend / security | Migrations, auth/ACL, jobs/outbox | Trust client tenant/groups |
| RAG / data | Docling, chunking, embeddings, citations | Fake page numbers |
| Frontend | React screens, typed client | Call Ollama/Qdrant directly |
| QA | Independent acceptance, failure tests | Mark not_run as passed |

## Hard rules

- Heavy data only on **D:** (WSL VHDX / Docker root). Keep **C: ≥ 50 GB** free.
- One LLM generation, one ingestion job in MVP.
- No cloud document upload; local Ollama with `OLLAMA_NO_CLOUD=1`.
- Prefer small end-to-end slices over unfinished giant scaffolds.
- Report GPU/WSL checks as passed/failed/not_run/blocked with evidence under `artifacts/<run-id>/`.
