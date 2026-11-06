# ADR 0001: Engine on WSL2, Valkey broker, React UI

**Status:** Accepted  
**Date:** 2026-10-30  
**Context:** RTX5060Ti 16GB / Windows 11 / 32GB RAM branch (`ROADMAP_local_RAG_RTX5060Ti_16GB.md` v1.3).

## Decisions

1. **Runtime:** Windows 11 → WSL2 Ubuntu-24.04 → Docker Engine + Compose plugin inside the distro. NVIDIA Container Toolkit for GPU containers. No Docker Desktop in the mandatory stack; do not run two daemons.
2. **Placement:** Working distro VHDX, Docker root, volumes, models, swap on **D:** (HDD). C: reserved for Windows (≥50 GB free).
3. **Broker:** Valkey (Redis protocol) for Celery transport; PostgreSQL remains source of truth for jobs via outbox. URI `redis://valkey:6379/0`, package extra `celery[redis]`.
4. **UI:** Own React + TypeScript + Vite SPA, static assets via nginx; FastAPI owns `/api`. No CDN. No permanent Node in runtime.
5. **LLM:** Ollama in Compose only; GPU dedicated to Ollama in MVP. Embeddings/Docling CPU-only.

## Consequences

- Bootstrap scripts target Ubuntu-24.04 on D:, not Docker Desktop paths.
- Celery/Valkey delivery must be smoke-tested before accepting the broker; on failure write a follow-up ADR.
- HDD latency affects cold model load, builds, OCR, and on-disk Qdrant; concurrency stays low (1 ingestion job, 1 LLM generation).
