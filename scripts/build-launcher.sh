#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/go/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/dist"
cd "$ROOT/apps/launcher"
if [[ ! -f go.mod ]]; then
  go mod init github.com/rag5060/launcher
fi
echo "building linux..."
go build -o "$ROOT/dist/RAG5060-linux-amd64" .
echo "building windows..."
GOOS=windows GOARCH=amd64 go build -o "$ROOT/dist/RAG5060.exe" .
ls -la "$ROOT/dist"
echo "go version: $(go version)"
