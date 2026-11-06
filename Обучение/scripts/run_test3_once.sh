#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
ROOT="/mnt/d/RAG 5060"
OB="$ROOT/Обучение"
cd "$ROOT"
THRESHOLD=0.55
ATTEMPT=10
sudo docker compose --profile core build api
sudo docker compose --profile core up -d api
bash scripts/wait-healthy.sh
ATTEMPT=$ATTEMPT TEST_N=3 python3 "$OB/scripts/apply_improvement.py"
ATTEMPT=$ATTEMPT TEST_N=3 THRESHOLD=$THRESHOLD OBUCHENIE_ROOT="$OB" python3 "$OB/scripts/eval_sample.py"
score=$(python3 -c "import json;print(json.load(open('$OB/artifacts/test3/attempt$ATTEMPT/summary.json'))['score'])")
echo SCORE=$score
python3 - <<PY
from pathlib import Path
p=Path("$OB/docs/RESULTS.md")
score="$score"
line=f"| Тест 3 | $ATTEMPT | sample | contain | {score} | $THRESHOLD | PASS | artifacts/test3/attempt$ATTEMPT |\n"
# status filled below
PY
if python3 -c "import json;import sys;sys.exit(0 if json.load(open('$OB/artifacts/test3/attempt$ATTEMPT/summary.json'))['passed'] else 1)"; then
  echo PASS
  python3 - <<PY
from pathlib import Path
p=Path("$OB/docs/RESULTS.md")
p.write_text(p.read_text(encoding="utf-8")+f"| Тест 3 | $ATTEMPT | sample | contain | $score | $THRESHOLD | PASS | artifacts/test3/attempt$ATTEMPT |\n", encoding="utf-8")
abil=Path("$OB/docs/ABILITIES.md")
lines=[]
for line in abil.read_text(encoding="utf-8").splitlines():
    if line.startswith("| A03 "):
        parts=[x.strip() for x in line.strip("|").split("|")]
        if len(parts)>=3:
            parts[2]=f"pass@$score"
            line="| "+" | ".join(parts)+" |"
    lines.append(line)
abil.write_text("\n".join(lines)+"\n", encoding="utf-8")
errs=Path("$OB/docs/ERRORS.md")
t=errs.read_text(encoding="utf-8")
# mark latest open as fixed
idx=t.rfind("- status: open\n")
if idx>=0:
    t=t[:idx]+"- status: fixed (test 3 passed attempt $ATTEMPT)\n"+t[idx+len("- status: open\n"):]
errs.write_text(t, encoding="utf-8")
PY
  # restore general
  python3 - <<'PY'
import json,subprocess
from pathlib import Path
CK="/tmp/rag-obuchenie.ck"; BASE="http://127.0.0.1:8080"; pw="admin"
for line in Path("/mnt/d/RAG 5060/.env").read_text(encoding="utf-8").splitlines():
    if line.startswith("ADMIN_PASSWORD="): pw=line.split("=",1)[1]
subprocess.check_call(["curl","-fsS","-c",CK,"-b",CK,"-X","POST",f"{BASE}/api/auth/login","-H","Content-Type: application/json","-d",json.dumps({"login":"admin","password":pw})], stdout=subprocess.DEVNULL)
subprocess.check_call(["curl","-fsS","-c",CK,"-b",CK,"-X","POST",f"{BASE}/api/admin/chat-eval-config","-H","Content-Type: application/json","-d",json.dumps({"chat_prompt_mode":"general","retrieve_limit":8,"retrieve_scan":600,"retrieval_filename_prefix":""})], stdout=subprocess.DEVNULL)
print("restored")
PY
else
  echo FAIL
  python3 - <<PY
from pathlib import Path
p=Path("$OB/docs/RESULTS.md")
p.write_text(p.read_text(encoding="utf-8")+f"| Тест 3 | $ATTEMPT | sample | contain | $score | $THRESHOLD | FAIL | artifacts/test3/attempt$ATTEMPT |\n", encoding="utf-8")
PY
  exit 1
fi
