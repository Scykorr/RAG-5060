#!/usr/bin/env bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
f=/root/.ollama/models/blobs/sha256-a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f-partial
echo "size1=$(sudo docker exec rag5060-ollama-1 stat -c%s $f 2>/dev/null || echo 0)"
sleep 8
echo "size2=$(sudo docker exec rag5060-ollama-1 stat -c%s $f 2>/dev/null || echo 0)"
echo "curl registry:"
curl -sI -m 15 https://registry.ollama.ai/ | head -5 || true
ps aux | grep '[o]llama pull' || echo 'no pull'
