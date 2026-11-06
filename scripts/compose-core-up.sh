#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
sudo /usr/bin/docker compose --profile core config > /tmp/compose-config.yml
echo "compose config OK lines=$(wc -l </tmp/compose-config.yml)"
sudo /usr/bin/docker compose --profile core pull
sudo /usr/bin/docker compose --profile core up -d
sudo /usr/bin/docker compose --profile core ps
df -h /
sudo /usr/bin/docker info 2>/dev/null | grep -E 'Docker Root Dir|Runtimes'
