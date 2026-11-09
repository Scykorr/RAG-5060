# ADR: Document Graph Explorer (MVP)

**Статус:** Accepted · 2026-11-09  
**Контекст:** ТЗ `CURSOR_PROMPT_RAG_DOCUMENT_GRAPH_OFFLINE.md`; аудит `01_REPOSITORY_AUDIT.md`.

## Решение

1. **Хранилище связей:** PostgreSQL таблицы `graph_edges`, `graph_evidence`, `graph_build_jobs`, `graph_review_events` (без Neo4j).
2. **UI:** вкладка «Граф связей» в существующем `apps/web` SPA; визуализация **Cytoscape.js 3.30.x (MIT)**, локальный vendor-файл (не CDN, не React-wrapper).
3. **API:** `/api/graph/*` на том же FastAPI + cookie auth.
4. **ACL:** идентично documents — `tenant_id` + hide deleted; мутации review/jobs — `admin`. Не вводим параллельную auth.
5. **Детекторы MVP:**
   - `VersionRelationDetector` — SAME_FAMILY по одинаковому `sha256` / lineage версий;
   - `ExplicitReferenceDetector` — упоминание уникального `original_name`/title в тексте;
   - `LexicalSimilarityDetector` — token Jaccard по чанкам (`score_method=token_jaccard_v1`), т.к. Qdrant сейчас с нулевыми векторами;
   - LLM / entity — **OFF** (P2).
6. **Jobs:** SQL state machine + daemon thread (как quality_loop), concurrency=1, idempotent UPSERT.
7. **Offline:** статика nginx уже локальная; cytoscape в `apps/web/vendor/`.

## Альтернативы отклонены

| Вариант | Почему нет |
| --- | --- |
| Neo4j / GraphRAG MS | Лишняя БД, облачные/enterprise риски |
| React rewrite UI | Ломает работающий SPA |
| Sigma.js параллельно | Не нужен для ≤500 узлов |
| Fake cosine на нулевых векторах | Ложная доказательность |
| CDN cytoscape | Нарушает offline |

## Последствия

- Граф честно показывает `DEGRADED_NO_VECTOR_SEARCH` пока embeddings не реальны.
- При появлении BGE-M3 можно добавить VectorNeighborAdapter без смены схемы edges.
- Rollback: `alembic downgrade` drop graph_* + revert UI files.

## Миграция / откат

- Upgrade: `20261109_0003_document_graph`.
- Downgrade: drop graph tables (данные связей теряются; documents/chunks нетронуты).
- Backup: `scripts/backup-mvp.sh` до migrate на проде.
