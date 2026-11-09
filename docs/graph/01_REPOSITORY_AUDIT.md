# Repository audit — Document Graph (2026-11-09)

Источник истины: **код и миграции в дереве**, не ROADMAP/ориентир ТЗ.

## 1. Дерево приложения (факт)

| Путь | Назначение |
| --- | --- |
| `apps/api/` | FastAPI 0.2.0: auth, admin, documents, chat, quality, models |
| `apps/api/models.py` | SQLAlchemy ORM |
| `apps/api/services/` | ingestion, folder_import, storage, quality_*, ollama, model_jobs |
| `apps/web/` | **Статический SPA** (`index.html` + `app.js` + `styles.css`), без React/Vite |
| `apps/launcher/` | Host launcher (отдельно) |
| `alembic/versions/` | `20261103_0001` initial, `20261106_0002` quality_loop |
| `docker-compose.yml` | profile `core`: postgres, valkey, qdrant, ollama, api, nginx |
| `configs/nginx.conf` | `127.0.0.1:8080` → static + `/api` → api:8000 |
| `tests/` | unit (auth, health, quality_scoring); **нет E2E Playwright** |
| `vendor/python-wheels*` | офлайн pip |
| Данные | Docker volumes на D: VHDX (`pgdata`, `ragdata`, …) |

**Отклонение от ориентира ТЗ:** frontend **не** React+TS+Vite; Celery в optional deps, но ingestion/quality работают **в процессе API** (threading / sync `process_job`).

## 2. API-маршруты (факт)

- Auth: `POST /api/auth/login`, logout, me, change-password — cookie `rag5060_session`
- Documents: `POST/GET /api/documents`, `GET/DELETE /api/documents/{id}`, inbox/import-folder, jobs
- Chat: `/api/chats`, messages, retrieve lexical
- Admin: system, storage, dispatcher, reset-rag, models
- Quality: `/api/admin/quality/*` (+ quality router)
- Health: `/api/health`, `/api/ready`

Нет: collections API, access_groups API, document versions list endpoint, graph.

## 3. Модель документов / ACL (факт vs схема docs)

### Реальные таблицы

`tenants`, `users`, `tenant_memberships(role: admin|member)`, `documents(tenant_id, owner_id, status, active_version_id, deleted_at)`, `document_versions(...)`, `chunks(...)`, `ingestion_jobs`, `outbox_events`, `app_settings`, chat_*, quality_*, model_import_jobs.

### ACL на чтение (код)

- Список/чтение документов: **любой authenticated user того же `tenant_id`**, без фильтра owner/groups.
- Удаление: admin **или** `owner_id == user.id`; иначе унифицированный 404.
- Таблиц `access_groups` / `document_access_groups` в миграциях **нет** (есть только в `docs/schemas/data-model.md` как план).

**BLOCKER-note (не блокирует MVP графа):** group ACL не реализован. Граф **не расширяет** доступ: те же tenant-фильтры + hide deleted. Review/rebuild — admin.

### Версии

- `documents.active_version_id` → опубликованная версия после ingestion `ready`.
- Soft-delete: `deleted_at` + `status=deleted`, `active_version_id=None`.

## 4. Embeddings / Qdrant (факт)

- Collection: `chunks_v1`, dim `EMBEDDING_DIM=1024`, cosine.
- Upsert в `ingestion._maybe_qdrant_upsert`: **векторы нулевые** `[0.0]*dim`; payload: tenant_id, document_id, version_id, ordinal.
- Chat retrieval: **lexical SQL** по `chunks.text` (`_retrieve` в `chat.py`), не ANN.

**Следствие для графа:** `SEMANTIC_SIMILAR` через Qdrant ANN сейчас бессмысленен. MVP: детерминированные связи + lexical token-Jaccard с честным `score_method=token_jaccard_v1`; статус `DEGRADED_NO_VECTOR_SEARCH` пока векторы нулевые.

## 5. Ingestion / события

- Upload → `create_upload` → sync `process_job` (parsing→chunking→ready).
- Outbox table существует; активный publisher для graph **не найден**.
- Хук: `after_ingestion_ready(tenant_id)` → quality_trigger. Граф подпишется аналогично.

## 6. Web UI

- Вкладки: chat, dispatcher, system, documents, storage, quality, models, settings.
- Темы: тёмный CSS vars (`--accent` teal, `--accent2` blue); системные шрифты; без CDN.
- Роутинг: `data-tab` panels, не React Router. Глубокие ссылки: добавить `#graph` / query `?tab=graph&doc=`.

## 7. Workers

- Нет отдельного celery worker в compose.
- Pattern: DB job row + background thread / sync call. Graph jobs — тот же подход + lease в SQL.

## 8. Ресурсы (замер 2026-11-09)

- C: 137 GB free (ok ≥50); D: ~1.4 TB free.
- Stack: `rag5060-*` containers Up; UI `http://127.0.0.1:8080`.
- Host Python 3.11; Node/npm на Windows **отсутствуют** → Cytoscape вендорим как `apps/web/vendor/cytoscape.min.js`.

## 9. Baseline tests

- `tests/unit/test_auth_login.py`, `test_health.py`, `test_quality_scoring.py`.
- Полный pytest против живой БД в контейнере — по возможности; unit graph без GPU.

## 10. Несовпадения с roadmap (не мигрируем стек)

| Ориентир | Факт | Решение графа |
| --- | --- | --- |
| React+Vite | Vanilla SPA | Вкладка в `apps/web` |
| Celery workers | In-process jobs | `graph_build_jobs` + thread |
| access_groups | Нет | Tenant ACL как у documents |
| Real BGE-M3 vectors | Zero vectors | Lexical similarity + degraded flag |
| Collections | Filename prefixes | Фильтр по prefix имени |

## Карта коммитов

1. docs audit+ADR + migration + models
2. graph services/detectors/API + tests
3. UI Cytoscape + nav + styles
4. hooks ingestion/delete + offline/licenses + skills/progress
