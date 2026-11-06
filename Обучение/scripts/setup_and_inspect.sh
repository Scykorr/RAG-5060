#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-pip python3-pyarrow >/tmp/apt-pyarrow.log || true
python3 -c 'import pyarrow; print("pyarrow", pyarrow.__version__)'
python3 "/mnt/d/RAG 5060/Обучение/scripts/inspect_datasets.py"
python3 "/mnt/d/RAG 5060/Обучение/scripts/inspect_rucos.py"
