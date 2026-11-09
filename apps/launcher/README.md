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
RAG5060 start   # preflight → compose up -d → wait /api/health=200 → open UI
RAG5060 stop    # compose down (volumes kept)
RAG5060 status
RAG5060 open    # open UI only if healthy
```

On Windows the console **stays open** until Enter (so errors are visible). Logs: `logs/launcher.log`.

Browser opens **only after** **3 consecutive** JSON `{"status":"ok"}` health checks (nginx 502 is not enough).  
Default UI: **http://127.0.0.1:18080** (`RAG_UI_PORT`). Do not use old `:8080`.  
Default login: `admin` / `admin`.

Optional env: `RAG_DATA_ROOT` / `DATA_ROOT` (passed into compose environment).

Windows uses Docker Engine inside WSL distro `Ubuntu-24.04` (prefers `docker` without interactive `sudo`). Linux uses native Docker.

Offline image pack (optional): `./scripts/export-offline-images.sh`.
