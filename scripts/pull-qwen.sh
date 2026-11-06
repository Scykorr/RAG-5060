#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
echo "Starting ollama pull qwen3:8b ..."
sudo docker exec rag5060-ollama-1 ollama pull qwen3:8b
echo "=== result ==="
sudo docker exec rag5060-ollama-1 ollama list
echo "DONE"
