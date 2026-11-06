#!/usr/bin/env bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
echo "=== procs in ollama ==="
sudo docker exec rag5060-ollama-1 ps aux
echo "=== logs tail ==="
sudo docker logs --tail 80 rag5060-ollama-1 2>&1
echo "=== nvidia ==="
sudo docker exec rag5060-ollama-1 nvidia-smi 2>&1 | head -40
echo "=== tags quick ==="
CIP=$(sudo docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' rag5060-ollama-1)
curl -sS -m 10 "http://${CIP}:11434/api/tags" || echo tags_fail
