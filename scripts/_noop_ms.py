from time import perf_counter
from uuid import UUID

from apps.api.db import SessionLocal
from apps.api.models import Document
from apps.api.services import graph_index as gi

db = SessionLocal()
try:
    tenant = db.query(Document.tenant_id).limit(1).scalar()
    t0 = perf_counter()
    out = gi.build_index_for_tenant(db, UUID(str(tenant)), force=False)
    ms = int((perf_counter() - t0) * 1000)
    print(f"NOOP_MS={ms} mode={out.get('build_mode')} lifecycle={out.get('stats', {}).get('lifecycle')}")
finally:
    db.close()
