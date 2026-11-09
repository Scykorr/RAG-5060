# Graph API

Prefix: `/api/graph` (cookie session, same as rest of RAG5060).

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| GET | `/status` | user | degraded vector flag |
| GET | `/overview` | user | ACL-visible counts |
| GET | `/nodes` | user | search, prefix, cursor, limit≤500 |
| GET | `/edges?node_ids=` | user | both ends ACL |
| GET | `/neighborhood/{document_id}` | user | depth 1–3 |
| GET | `/documents/{id}/relations` | user | table view |
| GET | `/edges/{id}` | user | detail |
| GET | `/edges/{id}/evidence` | user | snippets after ACL |
| POST | `/jobs` | admin | analyze/rebuild (+ dry_run) |
| GET | `/jobs/{id}` | owner/admin | progress |
| POST | `/jobs/{id}/cancel` | admin | |
| POST | `/edges/{id}/review` | admin | APPROVE/REJECT |
| POST | `/edges/manual` | admin | |
| GET | `/export` | admin | visible subset JSON |

Errors: FastAPI `detail` object with `code`, `message`, `retryable`. Uniform 404 for hidden nodes/edges.
