#!/usr/bin/env python3
"""Build connectivity index for a tenant and write a human review report.

Usage (inside API container or with PYTHONPATH=repo root + DB env):
  python scripts/graph_index_report.py [--tenant-id UUID] [--out DIR]

Writes artifacts/<run-id>/graph_index_report.md
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Connectivity index report")
    parser.add_argument("--tenant-id", default="", help="Tenant UUID (default: first tenant)")
    parser.add_argument("--out", default="", help="Output directory (default artifacts/<run-id>)")
    parser.add_argument("--force", action="store_true", help="Force rebuild even if cache hit")
    args = parser.parse_args()

    from uuid import UUID

    from apps.api.db import SessionLocal
    from apps.api.models import Tenant
    from apps.api.services import graph_index as gidx

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-graph-index")
    out_dir = Path(args.out) if args.out else ROOT / "artifacts" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        if args.tenant_id:
            tenant_id = UUID(args.tenant_id)
        else:
            row = db.query(Tenant).order_by(Tenant.created_at.asc()).first()
            if not row:
                print("No tenants in DB", file=sys.stderr)
                return 2
            tenant_id = row.id

        payload = gidx.build_index_for_tenant(db, tenant_id, force=bool(args.force))
        stats = payload.get("stats") or {}
        docs = payload.get("documents") or []
        edges = payload.get("edges") or []

        # Degree by content edges
        deg: Counter[str] = Counter()
        by_type: dict[str, list[dict]] = defaultdict(list)
        for e in edges:
            by_type[e.get("type") or "?"].append(e)
            if e.get("type") in ("refers_to", "shared_entity", "lex_similar", "co_cited"):
                deg[str(e.get("source"))] += 1
                deg[str(e.get("target"))] += 1

        doc_title = {d["document_id"]: d.get("title") or d.get("filename") for d in docs}
        top_connected = deg.most_common(20)

        lines: list[str] = []
        lines.append(f"# Graph connectivity index report")
        lines.append("")
        lines.append(f"- run_id: `{run_id}`")
        lines.append(f"- tenant_id: `{tenant_id}`")
        lines.append(f"- fingerprint: `{payload.get('fingerprint')}`")
        lines.append(f"- built_at: `{payload.get('built_at')}`")
        lines.append(f"- built_ms: `{payload.get('built_ms')}`")
        lines.append("")
        lines.append("## Totals")
        lines.append("")
        lines.append(f"- documents: **{stats.get('documents', len(docs))}**")
        lines.append(f"- themes: **{stats.get('themes')}** — {', '.join(stats.get('theme_list') or [])}")
        lines.append(f"- authors: **{stats.get('authors')}** — {', '.join((stats.get('author_list') or [])[:30])}")
        lines.append(f"- isolated_documents (no B/C/D/E edges): **{stats.get('isolated_documents')}**")
        iso_ratio = 0.0
        n_docs = max(1, int(stats.get("documents") or len(docs) or 1))
        iso_ratio = float(stats.get("isolated_documents") or 0) / n_docs
        lines.append(f"- isolated share: **{iso_ratio:.1%}**")
        lines.append("")
        lines.append("## Edges by type")
        lines.append("")
        for typ, cnt in sorted((stats.get("edge_counts_by_type") or {}).items(), key=lambda x: -x[1]):
            lines.append(f"- `{typ}`: **{cnt}**")
        lines.append("")
        lines.append("## Top-20 most connected documents (B/C/D/E degree)")
        lines.append("")
        if not top_connected:
            lines.append("_none_")
        for i, (did, d) in enumerate(top_connected, 1):
            lines.append(f"{i}. degree={d} — {doc_title.get(did, did)} (`{did[:8]}…`)")
        lines.append("")

        rng = random.Random(42)
        for typ in ("in_theme", "wrote", "refers_to", "shared_entity", "lex_similar", "co_cited"):
            sample = list(by_type.get(typ) or [])
            rng.shuffle(sample)
            sample = sample[:10]
            lines.append(f"## Examples: `{typ}` ({len(by_type.get(typ) or [])} total, show {len(sample)})")
            lines.append("")
            if not sample:
                lines.append("_none_")
                lines.append("")
                continue
            for e in sample:
                src = e.get("source")
                tgt = e.get("target")
                src_l = doc_title.get(src, src)
                tgt_l = doc_title.get(tgt, tgt)
                lines.append(
                    f"- **{src_l}** → **{tgt_l}** · weight=`{e.get('weight')}` · provenance=`{e.get('provenance')}`"
                )
            lines.append("")

        report_path = out_dir / "graph_index_report.md"
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # also dump raw index pointer
        (out_dir / "index_fingerprint.txt").write_text(str(payload.get("fingerprint") or ""), encoding="utf-8")
        print(f"REPORT {report_path}")
        print(f"STATS documents={stats.get('documents')} edges={sum((stats.get('edge_counts_by_type') or {}).values())} isolated={stats.get('isolated_documents')}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
