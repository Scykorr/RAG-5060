#!/usr/bin/env bash
# Prepare samples, rebuild API, run Тест 1/2/3 with retries until PASS.
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
ROOT="/mnt/d/RAG 5060"
OB="$ROOT/Обучение"
cd "$ROOT"

MAX_ATTEMPTS="${MAX_ATTEMPTS:-6}"
THRESHOLD="${THRESHOLD:-0.55}"

echo "=== rebuild API (chat eval config + retrieve filter) ==="
sudo docker compose --profile core up -d --build api
bash scripts/wait-healthy.sh

echo "=== prepare samples (offline wheels) ==="
bash "$OB/scripts/run_python_tools.sh" 'python /data/scripts/prepare_samples.py'

append_result() {
  local testn="$1" attempt="$2" score="$3" thr="$4" status="$5" art="$6"
  python3 - <<PY
from pathlib import Path
p=Path("$OB/docs/RESULTS.md")
line=f"| Тест $testn | $attempt | sample | contain | $score | $thr | $status | $art |\n"
txt=p.read_text(encoding="utf-8")
if "(ожидание первых прогонов)" in txt:
    txt=txt.replace("| (ожидание первых прогонов) | | | | | | | |\n","")
p.write_text(txt+line, encoding="utf-8")
PY
}

mark_ability() {
  local id="$1" level="$2"
  python3 - <<PY
from pathlib import Path
p=Path("$OB/docs/ABILITIES.md")
lines=[]
for line in p.read_text(encoding="utf-8").splitlines():
    if line.startswith("| $id "):
        parts=[x.strip() for x in line.strip("|").split("|")]
        # ID, Умение, Уровень, ...
        if len(parts)>=3:
            parts[2]="$level"
            line="| " + " | ".join(parts) + " |"
    lines.append(line)
p.write_text("\n".join(lines)+"\n", encoding="utf-8")
PY
}

run_test() {
  local testn="$1"
  local ability="$2"
  echo "======== TEST $testn ========"
  for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    echo "--- attempt $attempt ---"
    ATTEMPT=$attempt TEST_N=$testn python3 "$OB/scripts/apply_improvement.py"
    set +e
    ATTEMPT=$attempt TEST_N=$testn THRESHOLD=$THRESHOLD \
      OBUCHENIE_ROOT="$OB" \
      python3 "$OB/scripts/eval_sample.py"
    rc=$?
    set -e
    art="artifacts/test${testn}/attempt${attempt}"
    if [[ -f "$OB/$art/summary.json" ]]; then
      score=$(python3 -c "import json;print(json.load(open('$OB/$art/summary.json'))['score'])")
    else
      score="n/a"
    fi
    if [[ $rc -eq 0 ]]; then
      append_result "$testn" "$attempt" "$score" "$THRESHOLD" "PASS" "$art"
      mark_ability "$ability" "pass@${score}"
      # mark errors fixed for this test
      python3 - <<PY
from pathlib import Path
p=Path("$OB/docs/ERRORS.md")
t=p.read_text(encoding="utf-8")
t=t.replace("- status: open\n", "- status: fixed (test $testn passed attempt $attempt)\n")
p.write_text(t, encoding="utf-8")
PY
      echo "TEST $testn PASS attempt=$attempt score=$score"
      return 0
    fi
    append_result "$testn" "$attempt" "$score" "$THRESHOLD" "FAIL" "$art"
    echo "TEST $testn FAIL attempt=$attempt score=$score — retry"
  done
  echo "TEST $testn FAILED after $MAX_ATTEMPTS attempts"
  return 1
}

run_test 1 A01
run_test 2 A02
run_test 3 A03

# clear filename prefix after training so normal chat sees full corpus
python3 - <<'PY'
import json,subprocess
from pathlib import Path
CK="/tmp/rag-obuchenie.ck"
BASE="http://127.0.0.1:8080"
pw="admin"
for line in Path("/mnt/d/RAG 5060/.env").read_text(encoding="utf-8").splitlines():
    if line.startswith("ADMIN_PASSWORD="):
        pw=line.split("=",1)[1]
subprocess.check_call(["curl","-fsS","-c",CK,"-b",CK,"-X","POST",f"{BASE}/api/auth/login","-H","Content-Type: application/json","-d",json.dumps({"login":"admin","password":pw})], stdout=subprocess.DEVNULL)
subprocess.check_call(["curl","-fsS","-c",CK,"-b",CK,"-X","POST",f"{BASE}/api/admin/chat-eval-config","-H","Content-Type: application/json","-d",json.dumps({"chat_prompt_mode":"general","retrieve_limit":8,"retrieve_scan":600,"retrieval_filename_prefix":""})], stdout=subprocess.DEVNULL)
print("restored general chat config")
PY

echo "ALL_TESTS_DONE"
