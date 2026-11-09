# Offline install notes (Graph)

## Подготовка (нужен интернет один раз)

1. Обычный стек RAG5060 (compose images + `vendor/python-wheels`).
2. Cytoscape уже в репозитории: `apps/web/vendor/cytoscape.min.js` (MIT, 3.30.4).
3. `docker compose --profile core build api`.

## Эксплуатация без интернета

1. Поднять compose как обычно; UI: `http://127.0.0.1:8080/?tab=graph`.
2. Статика (`/`, `/app.js`, `/graph.js`, `/vendor/cytoscape.min.js`) отдаётся nginx с volume `./apps/web` — **без npm на runtime**.
3. API не ходит во внешние LLM/CDN для графа.
4. Внутренняя сеть docker/WSL должна оставаться доступной.

См. также `OFFLINE_TEST_REPORT.md`.
