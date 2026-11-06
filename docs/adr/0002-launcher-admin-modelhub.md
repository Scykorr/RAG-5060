# ADR 0002: Launcher 1A, Admin Console, Model Hub

**Status:** Accepted  
**Date:** 2026-11-03  
**Context:** User requires beautiful admin UI, load/storage views, LLM swap via GGUF/URL, single executable start, Windows+Linux portability.

## Decisions

1. **Launcher 1A:** thin native binary (`apps/launcher`, Go) starts/stops Docker Compose, runs preflight, opens browser. Not a monolith embedding Postgres/Qdrant/Ollama.
2. **Platforms v1:** Windows + Linux with NVIDIA. macOS out of scope.
3. **Admin Console (F11):** React SPA sections Dashboard / System / Storage / Models / Documents / Chat; all via `/api` and `/api/admin/*`.
4. **Model Hub (F13):** Ollama remains LLM runtime. Models enter via (a) Ollama tag pull, (b) local GGUF → Modelfile → `ollama create`, (c) URL download → same import. Active model stored in app settings; switch unloads previous VRAM resident.
5. **Portability:** portable directory + launcher artifact; `RAG_DATA_ROOT` for heavy data; optional offline image pack script.

## Consequences

- Docker Engine remains a host prerequisite (WSL Engine on this PC is valid).
- Launcher must detect Engine on Windows (WSL distro) and native Linux.
- GGUF quality/compatibility is user’s responsibility; smoke generate gates “active”.
