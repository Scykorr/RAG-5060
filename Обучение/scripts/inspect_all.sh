#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
OB="/mnt/d/RAG 5060/Обучение"

# Offline: vendor/python-wheels-tools (scripts/vendor-python-wheels.sh once online)
bash "$OB/scripts/run_python_tools.sh" \
  'python /data/scripts/inspect_datasets.py && python /data/scripts/inspect_rucos.py'
