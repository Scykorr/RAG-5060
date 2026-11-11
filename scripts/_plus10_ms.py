"""Measure incremental rebuild after 10 doc artifacts are invalidated.

Marks index stale so early pointer-noop does not short-circuit; only 10 docs
are re-scanned, global layers still recompute (real +10-doc path).
"""
from time import perf_counter
from uuid import UUID

from apps.api.db import SessionLocal
from apps.api.models import Document
from apps.api.services import graph_index as gi
from apps.api.services import graph_index_cache as gcache

db = SessionLocal()
try:
    tenant = db.query(Document.tenant_id).limit(1).scalar()
    tid = UUID(str(tenant))
    ddir = gcache.doc_cache_dir(tid)
    files = sorted(ddir.glob("*.json"))[:10]
    for p in files:
        p.unlink(missing_ok=True)
    gcache.mark_index_stale(tid, reason="plus10_bench")
    print(f"invalidated={len(files)}")
    t0 = perf_counter()
    out = gi.build_index_for_tenant(db, tid, force=False)
    ms = int((perf_counter() - t0) * 1000)
    st = out.get("stats") or {}
    layer = (st.get("layer_ms") or {})
    print(
        f"PLUS10_MS={ms} mode={st.get('build_mode')} "
        f"lifecycle={st.get('lifecycle')} scan_clean={layer.get('scan_clean')}"
    )
finally:
    db.close()
