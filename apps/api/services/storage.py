from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from apps.api.settings import get_settings


def data_root() -> Path:
    root = Path(get_settings().data_root)
    for sub in ("originals", "extracted", "imports", "scratch", "inbox"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def list_files(limit: int = 200) -> list[dict]:
    root = data_root()
    files: list[dict] = []
    for sub in ("originals", "extracted", "imports"):
        base = root / sub
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                st = p.stat()
                files.append(
                    {
                        "path": str(p.relative_to(root)).replace("\\", "/"),
                        "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                    }
                )
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files[:limit]


def disk_stats() -> dict:
    root = data_root()
    usage = shutil.disk_usage(root)
    return {
        "data_root": str(root),
        "free_gb": round(usage.free / (1024**3), 2),
        "total_gb": round(usage.total / (1024**3), 2),
        "used_gb": round(usage.used / (1024**3), 2),
    }


def storage_summary() -> dict:
    root = data_root()
    corpus = {"documents_active": 0, "chunks": 0}
    try:
        from sqlalchemy import func

        from apps.api.db import SessionLocal
        from apps.api.models import Chunk, Document

        db = SessionLocal()
        try:
            corpus["documents_active"] = int(
                db.query(func.count(Document.id))
                .filter(Document.deleted_at.is_(None), Document.status != "deleted")
                .scalar()
                or 0
            )
            corpus["chunks"] = int(db.query(func.count(Chunk.id)).scalar() or 0)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass
    return {
        "data_root": str(root),
        "originals_bytes": dir_size(root / "originals"),
        "extracted_bytes": dir_size(root / "extracted"),
        "imports_bytes": dir_size(root / "imports"),
        "files": list_files(),
        "corpus": corpus,
    }


def cleanup_scratch() -> dict:
    root = data_root()
    scratch = root / "scratch"
    removed = 0
    if scratch.exists():
        for p in scratch.iterdir():
            if p.is_file():
                p.unlink(missing_ok=True)
                removed += 1
            elif p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
    return {"message": f"Removed {removed} scratch items", "removed": removed}


def save_upload(relative_dir: str, filename: str, data: bytes) -> Path:
    safe = "".join(c for c in filename if c.isalnum() or c in "._- ")
    dest = data_root() / relative_dir / safe
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest
