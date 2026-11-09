# Offline test report — Graph (2026-11-09)

**Метод:** сервисы уже локальные; проверка отсутствия remote assets в UI графа.

| Проверка | Результат |
| --- | --- |
| `GET /vendor/cytoscape.min.js` | 200, локальный nginx |
| `GET /graph.js` | 200 |
| HTML/CSS без Google Fonts / CDN script | PASS (системные шрифты) |
| `GET /api/graph/status` после login | PASS, local API |
| External script tags в index.html | только `/vendor/...`, `/graph.js`, `/app.js` |

Полный firewall-block egress на хосте в этой сессии **не выполнялся** (чтобы не рвать WSL DNS); код-путь не содержит remote fetch для графа. Повтор с блокировкой egress рекомендуется оператору.

**Дата:** 2026-11-09
