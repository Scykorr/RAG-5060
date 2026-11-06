#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"

python3 <<'PY'
from pathlib import Path
import re
text = Path(".env").read_text(encoding="utf-8", errors="ignore")
pw_m = re.search(r"^POSTGRES_PASSWORD=(.*)$", text, re.M)
user_m = re.search(r"^POSTGRES_USER=(.*)$", text, re.M)
db_m = re.search(r"^POSTGRES_DB=(.*)$", text, re.M)
user = (user_m.group(1).strip() if user_m else "rag")
db = (db_m.group(1).strip() if db_m else "rag")
pw = pw_m.group(1).strip() if pw_m else "rag"
new_url = f"postgresql+psycopg://{user}:{pw}@postgres:5432/{db}"
lines = []
seen = False
for line in text.splitlines():
    if line.startswith("DATABASE_URL="):
        lines.append("DATABASE_URL=" + new_url)
        seen = True
    else:
        lines.append(line)
if not seen:
    lines.append("DATABASE_URL=" + new_url)
if not re.search(r"^ADMIN_PASSWORD=.+$", "\n".join(lines), re.M):
    lines.append("ADMIN_PASSWORD=admin-change-me")
Path(".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("synced DATABASE_URL; admin_password present")
PY

sudo docker compose --profile core stop api nginx postgres || true
sudo docker compose --profile core rm -f api nginx || true
sudo docker volume rm rag5060_pgdata || true
sudo docker compose --profile core up -d postgres
for i in $(seq 1 30); do
  if sudo docker exec rag5060-postgres-1 pg_isready -U rag >/dev/null 2>&1; then
    echo postgres_ready
    break
  fi
  sleep 1
done
sudo docker compose --profile core up -d --build api nginx
for i in $(seq 1 40); do
  st=$(sudo docker inspect -f '{{.State.Health.Status}}' rag5060-api-1 2>/dev/null || echo starting)
  echo "api_health=$st"
  if [[ "$st" == "healthy" ]]; then break; fi
  if [[ "$st" == "unhealthy" ]]; then sudo docker logs rag5060-api-1 2>&1 | tail -50; exit 1; fi
  sleep 2
done
sudo docker compose --profile core ps
curl -fsS http://127.0.0.1:8080/api/health || true
echo
