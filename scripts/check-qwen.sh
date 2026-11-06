#!/usr/bin/env bash
set -uo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
echo "=== containers ==="
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
echo "=== ollama list ==="
sudo docker exec rag5060-ollama-1 ollama list || echo "ollama exec failed"
echo "=== pull processes ==="
ps aux | grep '[o]llama pull' || echo "no pull running"
echo "=== disk ==="
df -h /
