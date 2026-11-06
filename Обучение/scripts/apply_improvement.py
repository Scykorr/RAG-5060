#!/usr/bin/env python3
"""Configure chat eval settings for a given attempt/test via admin API."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date
from pathlib import Path

ATTEMPT = int(os.environ.get("ATTEMPT", "1"))
TEST_N = int(os.environ.get("TEST_N", "1"))
BASE = os.environ.get("RAG_BASE", "http://127.0.0.1:8080")
CK = os.environ.get("RAG_COOKIE", "/tmp/rag-obuchenie.ck")
ERRORS = Path("/mnt/d/RAG 5060/Обучение/docs/ERRORS.md")


def curl_json(args: list[str]) -> dict:
    out = subprocess.check_output(["curl", "-fsS", "-m", "60", *args], text=True)
    return json.loads(out) if out else {}


def login() -> None:
    env = Path("/mnt/d/RAG 5060/.env")
    pw = "admin"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("ADMIN_PASSWORD="):
            pw = line.split("=", 1)[1]
            break
    curl_json(
        [
            "-c",
            CK,
            "-b",
            CK,
            "-X",
            "POST",
            f"{BASE}/api/auth/login",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps({"login": "admin", "password": pw}),
        ]
    )


def set_config(cfg: dict) -> dict:
    return curl_json(
        [
            "-c",
            CK,
            "-b",
            CK,
            "-X",
            "POST",
            f"{BASE}/api/admin/chat-eval-config",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(cfg),
        ]
    )


def log_error(symptom: str, cause: str, fix: str) -> None:
    entry = (
        f"\n## {date.today().isoformat()} — Тест {TEST_N} / attempt {ATTEMPT}\n"
        f"- symptom: {symptom}\n"
        f"- root_cause: {cause}\n"
        f"- fix: {fix}\n"
        f"- status: open\n"
    )
    ERRORS.write_text(ERRORS.read_text(encoding="utf-8") + entry, encoding="utf-8")


def main() -> None:
    login()
    # Тест 3: revised pack uses t3r2_ filenames (enriched cloze queries in docs).
    prefix = "t3r2_" if TEST_N == 3 else f"t{TEST_N}_"
    if TEST_N == 3:
        mode = "cloze"
    else:
        mode = "general" if ATTEMPT == 1 else "extractive"

    if ATTEMPT == 1:
        cfg = {
            "chat_prompt_mode": mode,
            "retrieve_limit": 8,
            "retrieve_scan": 600,
            "retrieval_filename_prefix": prefix,
        }
        note = "baseline + filename prefix isolation"
    elif ATTEMPT == 2:
        cfg = {
            "chat_prompt_mode": "extractive" if TEST_N != 3 else "cloze",
            "retrieve_limit": 10,
            "retrieve_scan": 800,
            "retrieval_filename_prefix": prefix,
        }
        note = "extractive/cloze prompt + wider scan"
        log_error("score below threshold", "generation/retrieval mismatch", note)
    elif ATTEMPT == 3:
        cfg = {
            "chat_prompt_mode": "extractive" if TEST_N != 3 else "cloze",
            "retrieve_limit": 12,
            "retrieve_scan": 1200,
            "retrieval_filename_prefix": prefix,
        }
        note = "more chunks + larger lexical scan"
        log_error("still failing", "missed contexts or verbose answers", note)
    else:
        cfg = {
            "chat_prompt_mode": "cloze" if TEST_N == 3 else "extractive",
            "retrieve_limit": 16,
            "retrieve_scan": 2000,
            "retrieval_filename_prefix": prefix,
        }
        note = "aggressive retrieval"
        log_error("repeated fail", "need aggressive retrieve", note)

    out = set_config(cfg)
    print("config", json.dumps(out, ensure_ascii=False), "note", note)


if __name__ == "__main__":
    main()
