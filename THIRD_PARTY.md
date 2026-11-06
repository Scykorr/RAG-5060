# Third-party components (draft — pin after smoke)

| Component | Planned version | License / SPDX | Source | Notes |
| --- | --- | --- | --- | --- |
| Ubuntu WSL | 24.04 LTS | — | Canonical via `wsl --install` | Hosted on D: VHDX |
| Docker Engine (Moby) | pin after install | Apache-2.0 | https://docs.docker.com/engine/install/ubuntu/ | Inside WSL |
| Compose plugin | v2.x pin | Apache-2.0 | Docker docs | |
| NVIDIA Container Toolkit | latest stable pin | Apache-2.0 / NVIDIA | https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/ | |
| PostgreSQL | 16 | PostgreSQL License | official image | RAM limit 1G |
| Valkey | pin after smoke | BSD-3-Clause | https://github.com/valkey-io/valkey | Celery broker |
| Qdrant | pin | Apache-2.0 | https://github.com/qdrant/qdrant | on-disk vectors |
| Ollama | pin | MIT | https://github.com/ollama/ollama | GPU; `OLLAMA_NO_CLOUD=1` |
| Qwen3-8B Q4_K_M | tag/digest TBD | Apache-2.0 (base weights) | Ollama / Qwen | Verify artifact |
| BGE-M3 | revision TBD | MIT (weights) | https://huggingface.co/BAAI/bge-m3 | CPU embeddings |
| FastAPI | pin | MIT | https://github.com/fastapi/fastapi | |
| SQLAlchemy / Alembic | 2.x / pin | MIT | | |
| Celery | pin + redis extra | BSD-3-Clause | | transport only |
| Docling | pin | MIT (+ model licenses) | https://github.com/docling-project/docling | CPU |
| sentence-transformers | pin | Apache-2.0 | | CPU torch index |
| React / Vite / MUI Core | pin | MIT / Apache-2.0 | | no MUI X Pro |
| nginx | pin | BSD-2-Clause | | reverse proxy |
| pytest / Ruff / Playwright / Vitest | pin | MIT / Apache-2.0 | | |

**Host proprietary:** Windows 11, NVIDIA driver 610.88 — not OSS runtime of the RAG app.

Digests and exact tags must be filled after first successful `docker compose pull/build` and model pull; do not leave `latest` in production compose.
