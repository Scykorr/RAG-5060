# API MVP contracts

All routes under `/api`. Auth from server session; never trust client `tenant_id` / groups.

## Auth / health

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/auth/login` | rate-limited; secure password hash |
| POST | `/api/auth/logout` | revoke session |
| GET | `/api/health` | liveness only |

## Documents / jobs

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/documents` | upload PDF/DOCX/TXT ≤25 MiB → 202 + document_id, version_id, job_id |
| GET | `/api/documents` | pagination/filters, ACL-scoped |
| GET | `/api/documents/{id}` | 404 for cross-tenant (no existence leak) |
| POST | `/api/documents/{id}/versions` | new version |
| PATCH | `/api/documents/{id}/acl` | admin |
| DELETE | `/api/documents/{id}` | tombstone + cleanup job |
| GET | `/api/documents/{id}/versions/{version_id}/original` | protected |
| GET | `/api/jobs/{id}` | stage/progress/attempts/safe_error |
| POST | `/api/jobs/{id}/retry` | no new logical version |
| GET | `/api/chunks/{id}` | protected text + provenance |

## Chat

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/chat` | ACL → dense retrieval → budget ≤4096 → Ollama → `answer`/`partial`/`not_found` + citations |

Global: 1 running generation, ≤4 queued app requests → 429/503 with clear message.

## Graph Explorer (existing)

ACL-scoped document graph stored in Postgres (`/api/graph/status`, `/nodes`, `/edges`, `/neighborhood/…`, jobs). Separate from the lexical connectivity index below.

## Connectivity index (lexical / filename / citations; no Qdrant)

Tenant from session only. Cache: `data_root()/graph/<tenant>_<fingerprint>_index.json`.

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/graph/index/rebuild` | rebuild index for current tenant; returns `stats` |
| GET | `/api/graph/index/stats` | cached `stats` or `{status:"missing"}` |
| GET | `/api/graph/index/document/{document_id}?limit=20` | neighbors grouped by edge type with `weight` + `provenance` |

Edge types: `in_theme`, `wrote`, `refers_to`, `shared_entity`, `lex_similar`, `co_cited`. Every edge has weight and provenance (no decorative links).
