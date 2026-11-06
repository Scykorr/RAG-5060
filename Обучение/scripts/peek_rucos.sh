#!/usr/bin/env bash
set -euo pipefail
sudo docker run --rm \
  -e OBUCHENIE_ROOT=/data \
  -v "/mnt/d/RAG 5060/Обучение:/data:ro" \
  python:3.12-slim-bookworm \
  python /data/scripts/peek_rucos_item.py
