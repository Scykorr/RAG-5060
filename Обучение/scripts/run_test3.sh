#!/usr/bin/env bash
# Rebuild API, refresh RuCoS sample/inbox, retry Тест 3 until PASS.
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
ROOT="/mnt/d/RAG 5060"
OB="$ROOT/Обучение"
cd "$ROOT"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-8}"
THRESHOLD="${THRESHOLD:-0.55}"

sudo docker compose --profile core build --no-cache api
sudo docker compose --profile core up -d api
bash scripts/wait-healthy.sh

WHICH=3 N_TEST3="${N_TEST3:-20}" bash "$OB/scripts/run_python_tools.sh" 'python /data/scripts/prepare_samples.py'

append_result() {
  local attempt="$1" score="$2" status="$3" art="$4"
  python3 - <<PY
from pathlib import Path
p=Path("$OB/docs/RESULTS.md")
line=f"| Тест 3 | $attempt | sample | contain | $score | $THRESHOLD | $status | $art |\n"
p.write_text(p.read_text(encoding="utf-8")+line, encoding="utf-8")
PY
}

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "======== TEST 3 attempt $attempt ========"
  ATTEMPT=$attempt TEST_N=3 python3 "$OB/scripts/apply_improvement.py"
  set +e
  ATTEMPT=$attempt TEST_N=3 THRESHOLD=$THRESHOLD OBUCHENIE_ROOT="$OB" \
    python3 "$OB/scripts/eval_sample.py"
  rc=$?
  set -e
  art="artifacts/test3/attempt${attempt}"
  score=$(python3 -c "import json;print(json.load(open('$OB/$art/summary.json'))['score'])")
  if [[ $rc -eq 0 ]]; then
    append_result "$attempt" "$score" "PASS" "$art"
    python3 - <<PY
from pathlib import Path
p=Path("$OB/docs/ABILITIES.md")
lines=[]
for line in p.read_text(encoding="utf-8").splitlines():
    if line.startswith("| A03 "):
        parts=[x.strip() for x in line.strip("|").split("|")]
        if len(parts)>=3:
            parts[2]="pass@$score"
            line="| "+" | ".join(parts)+" |"
    lines.append(line)
p.write_text("\n".join(lines)+"\n", encoding="utf-8")
errs=Path("$OB/docs/ERRORS.md")
t=errs.read_text(encoding="utf-8")
t=t.replace("- status: open\n", "- status: fixed (test 3 passed attempt $attempt)\n", 1)
errs.write_text(t, encoding="utf-8")
PY
    # restore general chat
    python3 - <<'PY'
import json,subprocess
from pathlib import Path
CK="/tmp/rag-obuchenie.ck"; BASE="http://127.0.0.1:8080"; pw="admin"
for line in Path("/mnt/d/RAG 5060/.env").read_text(encoding="utf-8").splitlines():
    if line.startswith("ADMIN_PASSWORD="): pw=line.split("=",1)[1]
subprocess.check_call(["curl","-fsS","-c",CK,"-b",CK,"-X","POST",f"{BASE}/api/auth/login","-H","Content-Type: application/json","-d",json.dumps({"login":"admin","password":pw})], stdout=subprocess.DEVNULL)
subprocess.check_call(["curl","-fsS","-c",CK,"-b",CK,"-X","POST",f"{BASE}/api/admin/chat-eval-config","-H","Content-Type: application/json","-d",json.dumps({"chat_prompt_mode":"general","retrieve_limit":8,"retrieve_scan":600,"retrieval_filename_prefix":""})], stdout=subprocess.DEVNULL)
print("restored general chat config")
PY
    echo "TEST 3 PASS attempt=$attempt score=$score"
    exit 0
  fi
  append_result "$attempt" "$score" "FAIL" "$art"
  echo "FAIL score=$score — retry"
done
echo "TEST 3 FAILED"
exit 1
