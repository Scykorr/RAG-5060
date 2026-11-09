# Rollback Graph module

## Схема БД

```bash
# внутри окружения с alembic + DATABASE_URL
alembic downgrade 20261106_0002
```

Downgrade удаляет только `graph_*` таблицы. Документы/чанки/чаты не затрагиваются.

Если таблицы созданы через `Base.metadata.create_all` без alembic stamp:

```sql
DROP TABLE IF EXISTS graph_review_events;
DROP TABLE IF EXISTS graph_build_jobs;
DROP TABLE IF EXISTS graph_evidence;
DROP TABLE IF EXISTS graph_edges;
```

## Код / UI

```bash
git checkout master -- apps/web apps/api alembic/versions/20261109_0003_document_graph.py
# или git revert <graph commits>
docker compose --profile core build api
docker compose --profile core up -d api nginx
```

## Backup перед миграцией

`scripts/backup-mvp.sh` (pg dump + ragdata).
