# Graph data model (implemented)

Tables (Alembic `20261109_0003`):

- `graph_edges` — document↔document relations with `edge_key` uniqueness per tenant
- `graph_evidence` — provenance (chunk ids, page, method, scores); snippets loaded on demand
- `graph_build_jobs` — QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/RETRYING
- `graph_review_events` — append-only expert actions

Relation types in MVP: `SEMANTIC_SIMILAR` (lexical Jaccard), `EXPLICIT_REFERENCE`, `SAME_FAMILY`, `MANUAL_LINK`.

Axes: `origin`, `review_state`, `score`+`score_method` kept independent.

FK documents exist; soft-hide via `graph_edges.deleted_at` on document tombstone.
