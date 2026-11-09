# Администрирование Graph Explorer

## Включение

Модуль включён в API по умолчанию (`GraphLimits.enabled=true`). LLM-анализ связей выключен.

## Первичный анализ

```bash
# dry-run объёма
curl -b cookie.txt -H 'Content-Type: application/json' \
  -d '{"mode":"rebuild_collection_graph","dry_run":true}' \
  http://127.0.0.1:8080/api/graph/jobs

# один документ (быстро)
curl -b cookie.txt -H 'Content-Type: application/json' \
  -d '{"mode":"rebuild_doc_graph","document_id":"<UUID>"}' \
  http://127.0.0.1:8080/api/graph/jobs
```

Полный `rebuild_collection_graph` ограничен 500 документами за job и может быть долгим на lexical scan — запускать осознанно.

## Пороги

Стартовый `lexical_min_score=0.22` (`apps/api/services/graph/settings.py`). Менять после калибровки на корпусе.

## Recovery

- Зависшие jobs: state в SQL `graph_build_jobs`; cancel через API.
- После удаления документа рёбра soft-hide (`deleted_at`).
- Rollback схемы: см. `ROLLBACK.md`.
