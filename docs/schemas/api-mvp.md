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
