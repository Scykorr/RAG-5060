#!/usr/bin/env bash
# Russian RAG chat smoke with outbound internet blocked for api+ollama netns.
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd "/mnt/d/RAG 5060"
ART="${1:-$(cat artifacts/.last_ru_corpus 2>/dev/null || echo artifacts/manual-ru-chat)}"
mkdir -p "$ART"
CK=/tmp/rag5060-ru.ck
ADMIN_PASSWORD=$(grep ^ADMIN_PASSWORD= .env | cut -d= -f2-)
BASE=http://127.0.0.1:8080

bash scripts/wait-healthy.sh >/dev/null
curl -fsS -c "$CK" -b "$CK" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"login\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}" >/dev/null

block_ext() {
  local cname="$1"
  local pid
  pid=$(sudo docker inspect -f '{{.State.Pid}}' "$cname")
  # Allow docker/private ranges + DNS to host gateway; drop the rest.
  sudo nsenter -t "$pid" -n iptables -F OUTPUT 2>/dev/null || true
  sudo nsenter -t "$pid" -n iptables -A OUTPUT -o lo -j ACCEPT
  sudo nsenter -t "$pid" -n iptables -A OUTPUT -d 10.0.0.0/8 -j ACCEPT
  sudo nsenter -t "$pid" -n iptables -A OUTPUT -d 172.16.0.0/12 -j ACCEPT
  sudo nsenter -t "$pid" -n iptables -A OUTPUT -d 192.168.0.0/16 -j ACCEPT
  sudo nsenter -t "$pid" -n iptables -A OUTPUT -d 127.0.0.0/8 -j ACCEPT
  sudo nsenter -t "$pid" -n iptables -A OUTPUT -j REJECT
  echo "blocked_ext $cname pid=$pid"
}

unblock_ext() {
  local cname="$1"
  local pid
  pid=$(sudo docker inspect -f '{{.State.Pid}}' "$cname" 2>/dev/null || echo "")
  [[ -n "$pid" ]] || return 0
  sudo nsenter -t "$pid" -n iptables -F OUTPUT 2>/dev/null || true
  echo "unblocked_ext $cname"
}

cleanup() {
  unblock_ext rag5060-api-1 || true
  unblock_ext rag5060-ollama-1 || true
}
trap cleanup EXIT

block_ext rag5060-api-1
block_ext rag5060-ollama-1

{
  echo -n "api_ext="; sudo docker exec rag5060-api-1 python3 -c 'import urllib.request; urllib.request.urlopen("https://example.com", timeout=3); print(200)' 2>/dev/null || echo blocked
  echo -n "ollama_ext="; sudo docker exec rag5060-ollama-1 python3 -c 'import urllib.request; urllib.request.urlopen("https://example.com", timeout=3); print(200)' 2>/dev/null || echo blocked
  echo -n "api_ollama="; sudo docker exec rag5060-api-1 python3 -c 'import urllib.request; print(urllib.request.urlopen("http://ollama:11434/api/tags", timeout=5).status)' 2>/dev/null || echo fail
} | tee "$ART/offline-net.txt"

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
    (art / f"chat-offline-{tag}.json").write_text(out, encoding="utf-8")
    d = json.loads(out)
    ans = d.get("answer") or ""
    print("Q:", q)
    print("outcome:", d.get("outcome"), "cites:", len(d.get("citations") or []))
    print("A:", ans[:500])
    assert d.get("outcome") in ("answer", "partial"), d
    assert d.get("citations"), d
    low = ans.lower().replace("ё", "е")
    missing = [n for n in needles if n.lower().replace("ё", "е") not in low]
    assert not missing, (missing, ans[:400])
    assert re.search(r"[А-Яа-яЁё]", ans), ans[:200]
    print("PASS", tag)
    print()

chat("Кто написал пьесу «Вишнёвый сад»?", ["чехов"], "cherry")

# Strict custom check: Ольга + Ирина + (Маша|Мария)
payload = json.dumps(
    {"question": "В пьесе Чехова «Три сестры» как зовут сестёр Прозоровых?"},
    ensure_ascii=False,
)
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
(art / "chat-offline-sisters.json").write_text(out, encoding="utf-8")
d = json.loads(out)
ans = d.get("answer") or ""
low = ans.lower().replace("ё", "е")
print("Q: Три сестры", "outcome:", d.get("outcome"), "cites:", len(d.get("citations") or []))
print("A:", ans[:500])
assert d.get("outcome") in ("answer", "partial"), d
assert d.get("citations"), d
assert "ольга" in low and "ирина" in low and (("маша" in low) or ("мари" in low)), ans[:400]
assert re.search(r"[А-Яа-яЁё]", ans)
print("PASS sisters")
print()

chat("Кто автор романа «Анна Каренина»?", ["толст"], "anna")
print("OFFLINE_RU_CHAT_OK")
PY

echo PASS | tee "$ART/OFFLINE_PASS.txt"
