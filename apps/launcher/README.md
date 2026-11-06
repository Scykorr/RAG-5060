# RAG5060 Launcher (1A)

Single binary that starts/stops the Docker Compose stack and opens the web UI.

## Build

```bash
# Linux / WSL
cd apps/launcher
GOOS=linux GOARCH=amd64 go build -o ../../dist/RAG5060-linux-amd64 .
GOOS=windows GOARCH=amd64 go build -o ../../dist/RAG5060.exe .
```

## Usage

Place the binary next to `docker-compose.yml` (or keep portable layout from repo root).

```text
RAG5060 start   # preflight (Docker/GPU/disk/port) → compose up → open UI
RAG5060 stop    # compose down (volumes kept)
RAG5060 status
RAG5060 open
```

Optional env: `RAG_DATA_ROOT` / `DATA_ROOT` (passed into compose environment).

Windows uses Docker Engine inside WSL distro `Ubuntu-24.04`. Linux uses native Docker.

Offline image pack (optional): `./scripts/export-offline-images.sh`.
