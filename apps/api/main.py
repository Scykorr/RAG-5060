"""RAG5060 API — health, admin, models, documents, chat."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.bootstrap import init_db
from apps.api.routers import admin, auth, chat, documents, graph, models, quality
from apps.api.settings import get_settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    log.info("api_start")
    yield
    log.info("api_stop")


app = FastAPI(title="RAG5060 API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(models.router)
app.include_router(quality.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(graph.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


async def _check_http(url: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
            return {"ok": r.status_code < 500, "status_code": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


@app.get("/api/ready")
async def ready() -> JSONResponse:
    settings = get_settings()
    checks: dict[str, Any] = {}
    checks["qdrant"] = await _check_http(f"{settings.qdrant_url.rstrip('/')}/readyz")
    checks["ollama"] = await _check_http(f"{settings.ollama_host.rstrip('/')}/api/tags")

    try:
        import asyncpg

        dsn = settings.database_url_sync.replace("postgresql+psycopg://", "postgresql://")
        conn = await asyncpg.connect(dsn=dsn, timeout=3)
        try:
            await conn.execute("SELECT 1")
            checks["postgres"] = {"ok": True}
        finally:
            await conn.close()
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = {"ok": False, "error": type(exc).__name__}

    try:
        import redis.asyncio as redis

        client = redis.from_url(settings.valkey_url, socket_connect_timeout=2)
        pong = await client.ping()
        await client.aclose()
        checks["valkey"] = {"ok": bool(pong)}
    except Exception as exc:  # noqa: BLE001
        checks["valkey"] = {"ok": False, "error": type(exc).__name__}

    required = ("postgres", "valkey", "qdrant")
    all_ok = all(bool(checks.get(k, {}).get("ok")) for k in required)
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
    )
