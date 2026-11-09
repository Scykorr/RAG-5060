# Release notes — Document Graph Explorer (MVP)

**Date:** 2026-11-09  
**Branch:** `feature/document-graph`

- Вкладка «Граф связей» в существующем SPA (`/?tab=graph`)
- Cytoscape.js 3.30.4 MIT, локальный vendor
- PostgreSQL `graph_*` + FastAPI `/api/graph/*` + ACL tenant
- Детекторы: SAME_FAMILY (sha256), EXPLICIT_REFERENCE (unique title), SEMANTIC_SIMILAR (`token_jaccard_v1`)
- Idempotent jobs; soft-hide on document delete; expert review (admin)
- LLM relation extraction: OFF
