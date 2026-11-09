# Performance notes — Graph MVP

**Machine:** local RAG5060 host (WSL2 Docker on D:), corpus ~702 docs.

| Scenario | Observation |
| --- | --- |
| `/api/graph/status` | < 1 s (incl. Qdrant scroll sample) |
| `rebuild_doc_graph` (1 doc vs ~400 peers lexical) | ~seconds; SUCCEEDED in smoke |
| `/neighborhood` depth=1 after build | < 1 s |
| UI Cytoscape 2 nodes / 1 edge | interactive |
| Collection rebuild dry_run | reports cap 500 |

**Bottleneck:** lexical pairwise scan is O(docs × candidates × chunks); acceptable for pilot, not for silent full-corpus button spam. Keep `GRAPH_BUILD_CONCURRENCY=1`.

p95 neighborhood target ≤2 s: **met** on smoke corpus after edges exist.
