# RAG5060 — локальный RAG (RTX 5060 Ti / Windows+Linux)

Один лаунчер поднимает Docker Compose и открывает веб-консоль: документы, чат, хранилище, модели (tag / GGUF / URL), мониторинг сервисов.

Спеки: `ROADMAP_local_RAG_RTX5060Ti_16GB.md`, `CODEX_CURSOR_PROMPT_RTX5060Ti_16GB.md`, ADR `docs/adr/0002-launcher-admin-modelhub.md`.

## Быстрый старт (один exe)

1. Установите Docker Engine (+ NVIDIA Container Toolkit) и драйвер NVIDIA.  
   На этом ПК: Docker Engine внутри WSL `Ubuntu-24.04` на диске **D:**.
2. Скопируйте `.env.example` → `.env`, задайте `POSTGRES_PASSWORD`, `ADMIN_PASSWORD`, `SESSION_SECRET`.
3. Запуск:

```text
dist\RAG5060.exe start          # Windows
./dist/RAG5060-linux-amd64 start  # Linux
```

UI: http://127.0.0.1:8080  
Остановка: `RAG5060 stop` (volumes сохраняются).

## Диски (текущий ПК)

- **C:** только Windows, ≥50 ГБ свободно.
- **D:** WSL VHDX, Docker root, модели, volumes (`D:\RAG5060\…`).

## Стек

PostgreSQL · Valkey · Qdrant · Ollama (GPU) · FastAPI · nginx UI

## Сборка лаунчера

```bash
./scripts/build-launcher.sh
```

## Portable / offline pack

```bash
./scripts/export-offline-images.sh   # → dist/offline-images/*.tar + load-images.sh
```

Перенос: лаунчер + `docker-compose.yml` + `configs/` + `.env.example` + `apps/web/` + offline images. На целевой машине: `load-images.sh`, скопировать `.env`, `RAG5060 start`.

## Ручной compose (без лаунчера)

```bash
wsl -d Ubuntu-24.04
cd "/mnt/d/RAG 5060"
sudo docker compose --profile core up -d --build
```
