#!/usr/bin/env bash
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
sudo /usr/bin/docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
echo '---'
sudo /usr/bin/docker compose --profile core ps
echo '---'
df -h /
Get-PSDrive 2>/dev/null || true
