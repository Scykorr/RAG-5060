from __future__ import annotations

import subprocess
from pathlib import Path

import httpx

from apps.api.settings import get_settings


def _host() -> str:
    return get_settings().ollama_host.rstrip("/")


def list_models() -> list[dict]:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(f"{_host()}/api/tags")
        r.raise_for_status()
        models = r.json().get("models", [])
        out = []
        for m in models:
            out.append(
                {
                    "name": m.get("name"),
                    "size": str(m.get("size", "")),
                    "digest": (m.get("digest") or "")[:64],
                    "modified_at": m.get("modified_at"),
                }
            )
        return out


def pull_model(name: str) -> None:
    with httpx.Client(timeout=None) as client:
        with client.stream("POST", f"{_host()}/api/pull", json={"name": name, "stream": False}) as r:
            r.raise_for_status()
            for _ in r.iter_bytes():
                pass


def generate(prompt: str, model: str, *, think: bool = False) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": get_settings().ollama_context_length},
    }
    # think=false for short RAG answers when supported
    payload["think"] = think
    with httpx.Client(timeout=300.0) as client:
        r = client.post(f"{_host()}/api/generate", json=payload)
        r.raise_for_status()
        return r.json().get("response", "")


def ps() -> dict:
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{_host()}/api/ps")
            if r.status_code >= 400:
                return {"ok": False, "error": f"status {r.status_code}"}
            return {"ok": True, "data": r.json()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def health() -> dict:
    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get(f"{_host()}/api/tags")
            return {"ok": r.status_code < 500, "status_code": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def create_from_gguf(name: str, gguf_path: Path) -> None:
    modelfile = gguf_path.parent / f"Modelfile.{name}"
    modelfile.write_text(f'FROM "{gguf_path.as_posix()}"\n', encoding="utf-8")
    # Use CLI inside network via HTTP create API
    with httpx.Client(timeout=None) as client:
        # Ollama create from Modelfile content
        content = modelfile.read_text(encoding="utf-8")
        r = client.post(f"{_host()}/api/create", json={"name": name, "modelfile": content, "stream": False})
        if r.status_code >= 400:
            # fallback: docker exec if running beside ollama container is not available from API container
            raise RuntimeError(r.text)
        for _ in r.iter_bytes() if hasattr(r, "iter_bytes") else []:
            pass


def unload(model: str) -> None:
    try:
        with httpx.Client(timeout=30.0) as client:
            client.post(
                f"{_host()}/api/generate",
                json={"model": model, "keep_alive": 0, "prompt": "", "stream": False},
            )
    except Exception:
        pass
