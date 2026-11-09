# Graph test report — 2026-11-09

## Unit

```text
docker run ... python -m pytest tests/graph -q
7 passed
```

## API smoke (live stack `127.0.0.1:8080`)

- Login admin → `/api/graph/status` → `DEGRADED_NO_VECTOR_SEARCH`, 702 docs
- `rebuild_doc_graph` on `sample-b.txt` → SUCCEEDED
- Neighborhood: edge `SEMANTIC_SIMILAR` sample.txt ↔ sample-b.txt, score 0.875, `token_jaccard_v1`
- Evidence: chunk snippets from both sides
- Double rebuild → `edges_stored` remains **1** (idempotent)
- Unauthenticated `/api/graph/status` → 401
- Chat create still works

Scripts: `scripts/smoke-graph.sh`, `scripts/smoke-graph-evidence.sh`

## Not run / partial

- Full Playwright E2E (tool not in project)
- Host firewall egress block (see OFFLINE_TEST_REPORT)
- Full 500-doc collection rebuild timing (dry_run only: 500)
- Real BGE-M3 vectors (platform still upserts zeros)
