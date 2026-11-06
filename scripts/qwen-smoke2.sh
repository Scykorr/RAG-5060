#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

# Stop hung interactive run so API can use the loaded runner
sudo docker exec rag5060-ollama-1 sh -c 'pkill -f "ollama run" || true'
sleep 2

CIP=$(sudo docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' rag5060-ollama-1)
URL="http://${CIP}:11434"

echo "=== generate ==="
# qwen3 thinking can be long; cap tokens and ask for short answer
curl -sS -m 120 "$URL/api/generate" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:8b","prompt":"/no_think\nSay only: OK","stream":false,"options":{"num_predict":32,"temperature":0}}' \
  | tee /tmp/qwen-smoke.json
echo
python3 - <<'PY'
import json
d=json.load(open('/tmp/qwen-smoke.json'))
print('response=', repr(d.get('response')))
print('eval_count=', d.get('eval_count'), 'eval_duration_ns=', d.get('eval_duration'))
print('done=', d.get('done'), 'done_reason=', d.get('done_reason'))
PY

echo "=== gpu ==="
sudo docker exec rag5060-ollama-1 nvidia-smi --query-gpu=name,memory.used,utilization.gpu --format=csv,noheader
