#!/usr/bin/env bash
# Import Russian corpus and verify offline Russian RAG Q&A.
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-ru-corpus}"
ART="artifacts/${RUN_ID}"
mkdir -p "$ART"
CK=/tmp/rag5060-ru.ck
rm -f "$CK"
ADMIN_PASSWORD=$(grep ^ADMIN_PASSWORD= .env | cut -d= -f2-)
BASE=http://127.0.0.1:8080
SUBDIR="${SUBDIR:-ru_corpus}"

bash scripts/wait-healthy.sh | tee "$ART/health.txt"

echo "=== offline env flags ==="
sudo docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' rag5060-ollama-1 \
  | grep -E 'OLLAMA_NO_CLOUD|OLLAMA_HOST' | tee "$ART/ollama-env.txt"
grep -q 'OLLAMA_NO_CLOUD=1' "$ART/ollama-env.txt"

curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"login\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}" >/dev/null

echo "=== inbox $SUBDIR ==="
curl -fsS -c "$CK" -b "$CK" "$BASE/api/documents/inbox?subdir=${SUBDIR}" | tee "$ART/inbox.json"
python3 -c "import json; d=json.load(open('$ART/inbox.json')); print('files', d.get('count')); assert (d.get('count') or 0) >= 100, d"

echo "=== import folder ==="
curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/documents/import-folder" \
  -H 'Content-Type: application/json' \
  -d "{\"subdir\":\"${SUBDIR}\",\"recursive\":true,\"skip_existing\":true}" | tee "$ART/import.json"

for i in $(seq 1 3600); do
  curl -fsS -c "$CK" -b "$CK" "$BASE/api/documents/import-folder/status" > "$ART/import-status.json"
  if python3 -c "import json; d=json.load(open('$ART/import-status.json')); print(d.get('running'), d.get('done'), d.get('skipped'), d.get('failed'), '/', d.get('total'), d.get('current')); raise SystemExit(0 if not d.get('running') else 1)"; then
    break
  fi
  sleep 3
done

python3 - <<PY
import json
d=json.load(open("$ART/import-status.json"))
print("FINAL", {k:d.get(k) for k in ("running","done","skipped","failed","total","error")})
assert not d.get("running"), d
done=(d.get("done") or 0)+(d.get("skipped") or 0)
assert done >= 100, d
assert (d.get("failed") or 0) <= max(10, int(0.1*(d.get("total") or 0))), d
PY

curl -fsS -c "$CK" -b "$CK" "$BASE/api/admin/dispatcher" | tee "$ART/dispatcher.json" >/dev/null
python3 - <<PY
import json
d=json.load(open("$ART/dispatcher.json"))
print("docs_active", d["corpus"]["documents_active"], "chunks", d["corpus"]["chunks"], "services_ok", d["services_ok"])
assert d["services_ok"] is True
assert d["corpus"]["documents_active"] >= 100
assert d["corpus"]["chunks"] >= 100
PY

echo "=== external network probes (host may have net; containers should not need it) ==="
{
  echo -n "host_ext="; curl -sS -m 5 -o /dev/null -w '%{http_code}' https://example.com || echo blocked
  echo
  sudo docker exec rag5060-api-1 sh -c 'echo -n api_ext=; curl -sS -m 4 -o /dev/null -w "%{http_code}" https://example.com || echo blocked'
  echo
  sudo docker exec rag5060-ollama-1 sh -c 'echo -n ollama_ext=; curl -sS -m 4 -o /dev/null -w "%{http_code}" https://registry.ollama.ai || echo blocked'
  echo
  sudo docker exec rag5060-ollama-1 printenv OLLAMA_NO_CLOUD
} | tee "$ART/net-probes.txt"

echo "=== Russian chat smoke (local LLM) ==="
python3 - <<PY
import json, subprocess, re
from pathlib import Path
art = Path("$ART")
base = "$BASE"
ck = "$CK"

def chat(q, needles, tag):
    payload = json.dumps({"question": q}, ensure_ascii=False)
    out = subprocess.check_output(
        [
            "curl", "-fsS", "-m", "240",
            "-c", ck, "-b", ck,
            "-X", "POST", f"{base}/api/chat",
            "-H", "Content-Type: application/json",
            "-d", payload,
        ],
        text=True,
    )
    (art / f"chat-{tag}.json").write_text(out, encoding="utf-8")
    d = json.loads(out)
    ans = d.get("answer") or ""
    print("Q:", q)
    print("outcome:", d.get("outcome"), "cites:", len(d.get("citations") or []), "model:", d.get("model"))
    print("A:", ans[:500])
    assert d.get("outcome") in ("answer", "partial"), d
    assert d.get("citations"), d
    low = ans.lower().replace("ё", "е")
    missing = [n for n in needles if n.lower().replace("ё", "е") not in low]
    assert not missing, (missing, ans[:400])
    assert re.search(r"[А-Яа-яЁё]", ans), "answer must contain Cyrillic"
    print("PASS", tag)
    print()

chat(
    "Кто написал пьесу «Вишнёвый сад»?",
    ["чехов", "антон"],
    "cherry-orchard",
)
chat(
    "В пьесе Чехова «Три сестры» как зовут сестёр Прозоровых? Назовите Ольгу, Машу и Ирину.",
    ["ольга", "маша", "ирина"],
    "three-sisters",
)
chat(
    "Кто автор романа «Анна Каренина»?",
    ["толст"],
    "anna-karenina",
)
print("CHAT_RU_OK")
PY

curl -fsS -c "$CK" -b "$CK" "$BASE/api/admin/dispatcher" > "$ART/dispatcher-after.json"
echo PASS | tee "$ART/PASS.txt"
echo "artifacts=$ART"
echo "$ART" > artifacts/.last_ru_corpus
