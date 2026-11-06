#!/usr/bin/env bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
echo "=== list ==="
sudo docker exec rag5060-ollama-1 ollama list
echo "=== ollama dir ==="
sudo docker exec rag5060-ollama-1 du -sh /root/.ollama || true
echo "=== blobs ==="
sudo docker exec rag5060-ollama-1 ls -lh /root/.ollama/models/blobs 2>/dev/null | tail -20 || true
echo "=== pull procs ==="
ps aux | grep '[o]llama pull' || echo none
