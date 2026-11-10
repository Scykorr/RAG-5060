#!/usr/bin/env python3
"""Build connectivity index and write a human review report (slice 1b)."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f))


def _load_prev_stats(artifacts: Path) -> dict | None:
    runs = sorted(artifacts.glob("*-graph-index/graph_index_report.md"))
    if len(runs) < 1:
        return None
    # prefer previous run's companion stats json if present
    for report in reversed(runs[-5:]):
        side = report.parent / "stats.json"
        if side.exists():
            try:
                return json.loads(side.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Connectivity index report")
    parser.add_argument("--tenant-id", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    from uuid import UUID

    from apps.api.db import SessionLocal
    from apps.api.models import Tenant
    from apps.api.services import graph_index as gidx

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-graph-index")
    out_dir = Path(args.out) if args.out else ROOT / "artifacts" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    prev = _load_prev_stats(ROOT / "artifacts")

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
        doc_title = {
            d["document_id"]: d.get("title") or d.get("filename") for d in docs
        }
        work_title = {}
        for d in docs:
            work_title[d.get("work_key")] = (
                f"{d.get('author_key') or '—'} / {d.get('title')}"
            )

        deg = Counter()
        work_deg = Counter()
        by_type: dict[str, list[dict]] = defaultdict(list)
        phrase_hub = Counter()
        entity_df = Counter()
        for e in edges:
            typ = e.get("type") or "?"
            by_type[typ].append(e)
            if typ in ("refers_to", "shared_entity", "lex_similar", "co_cited", "co_cited_eval"):
                deg[str(e.get("source"))] += 1
                deg[str(e.get("target"))] += 1
            if typ == "refers_to":
                ph = (e.get("provenance") or {}).get("phrase") or ""
                if ph:
                    phrase_hub[ph] += 1
            if typ == "shared_entity":
                for ent in (e.get("provenance") or {}).get("entities") or []:
                    entity_df[ent] += 1
            if typ.startswith("work_") and typ != "work_meta":
                work_deg[str(e.get("source"))] += 1
                work_deg[str(e.get("target"))] += 1

        deg_vals = sorted(deg.values())
        lines: list[str] = []
        lines.append("# Graph connectivity index report (slice 1b)")
        lines.append("")
        lines.append(f"- run_id: `{run_id}`")
        lines.append(f"- tenant_id: `{tenant_id}`")
        lines.append(f"- fingerprint: `{payload.get('fingerprint')}`")
        lines.append(f"- index_version: `{payload.get('version')}`")
        lines.append(f"- built_at: `{payload.get('built_at')}`")
        lines.append(f"- built_ms: `{payload.get('built_ms')}`")
        lines.append("")
        lines.append("## Totals")
        lines.append("")
        lines.append(f"- documents: **{stats.get('documents')}**")
        lines.append(f"- works: **{stats.get('works')}**")
        lines.append(f"- fragment_documents: **{stats.get('fragment_documents')}**")
        lines.append(f"- documents_without_author: **{stats.get('documents_without_author')}**")
        lines.append(f"- themes: **{stats.get('themes')}** — {', '.join(stats.get('theme_list') or [])}")
        lines.append(f"- authors: **{stats.get('authors')}** — {', '.join((stats.get('author_list') or [])[:30])}")
        lines.append(
            f"- isolated_documents: **{stats.get('isolated_documents')}** "
            f"({(stats.get('isolated_documents') or 0) / max(1, stats.get('documents') or 1):.1%})"
        )
        lines.append(
            f"- isolated_works: **{stats.get('isolated_works')}** "
            f"(share={stats.get('isolated_work_share')})"
        )
        lines.append("")
        if prev:
            lines.append("## Before / after (edge counts)")
            lines.append("")
            prev_ec = (prev.get("edge_counts_by_type") or {})
            cur_ec = stats.get("edge_counts_by_type") or {}
            keys = sorted(set(prev_ec) | set(cur_ec))
            for k in keys:
                lines.append(f"- `{k}`: {prev_ec.get(k, 0)} → **{cur_ec.get(k, 0)}**")
            lines.append(
                f"- isolated_documents: {prev.get('isolated_documents')} → **{stats.get('isolated_documents')}**"
            )
            lines.append(
                f"- isolated_works: {prev.get('isolated_works')} → **{stats.get('isolated_works')}**"
            )
            lines.append("")

        lines.append("## Edges by type")
        lines.append("")
        for typ, cnt in sorted((stats.get("edge_counts_by_type") or {}).items(), key=lambda x: -x[1]):
            lines.append(f"- `{typ}`: **{cnt}**")
        lines.append("")
        lines.append("## Degree distribution (textual doc edges)")
        lines.append("")
        if deg_vals:
            lines.append(f"- median: **{_percentile(deg_vals, 0.5):.0f}**")
            lines.append(f"- p90: **{_percentile(deg_vals, 0.9):.0f}**")
            lines.append(f"- max: **{max(deg_vals)}**")
        else:
            lines.append("_none_")
        lines.append("")
        lines.append("## Top-20 works by fragment_count")
        lines.append("")
        for row in stats.get("top_works_by_fragments") or []:
            lines.append(
                f"- frag={row.get('fragment_count')} — {row.get('author_key') or '—'} / {row.get('title')} (`{row.get('work_key')}`)"
            )
        lines.append("")
        lines.append("## Top-20 works by connectivity degree")
        lines.append("")
        for i, (wk, d) in enumerate(work_deg.most_common(20), 1):
            key = wk.removeprefix("work:")
            lines.append(f"{i}. degree={d} — {work_title.get(key, key)}")
        lines.append("")
        lines.append("## Top-30 refers_to phrases (noise detector)")
        lines.append("")
        for ph, n in phrase_hub.most_common(30):
            lines.append(f"- {n}× `{ph}`")
        lines.append("")
        lines.append("## Top-30 shared_entity surfaces (noise detector)")
        lines.append("")
        for ent, n in entity_df.most_common(30):
            lines.append(f"- {n}× `{ent}`")
        lines.append("")

        lex_debug = stats.get("lex_debug") or {}
        lines.append("## lex_similar debug")
        lines.append("")
        lines.append(f"- bugfix: {lex_debug.get('bugfix')}")
        lines.append(f"- score_count: {lex_debug.get('score_count')}")
        lines.append(f"- score_min/max: {lex_debug.get('score_min')} / {lex_debug.get('score_max')}")
        lines.append(f"- threshold: {lex_debug.get('threshold')}")
        lines.append(f"- edges: {lex_debug.get('edges')}")
        hist = lex_debug.get("score_hist") or {}
        if hist:
            lines.append("- histogram (score→count):")
            for k, v in list(hist.items())[:25]:
                lines.append(f"  - {k}: {v}")
        # near-threshold examples
        lex_edges = list(by_type.get("lex_similar") or [])
        thr = float(lex_debug.get("threshold") or 0.08)
        near = sorted(lex_edges, key=lambda e: abs(float(e.get("weight") or 0) - thr))[:10]
        lines.append("- examples near threshold:")
        for e in near:
            lines.append(
                f"  - w={e.get('weight')} {doc_title.get(e.get('source'), e.get('source'))} ↔ "
                f"{doc_title.get(e.get('target'), e.get('target'))} · {e.get('provenance')}"
            )
        lines.append("")

        lines.append("## co_cited note")
        lines.append("")
        lines.append(
            "- Quality loop (`quality_loop.evaluate_once`) does **not** persist ChatMessage rows; "
            "citations there are in-memory only. Index co_cited comes from ChatMessage+ChatSession."
        )
        lines.append(
            "- Sessions titled `*-smoke` / `ping` / matching `eval|quality` are treated as eval "
            "(`co_cited_eval` only if `include_eval_cocited=True`; default off)."
        )
        lines.append("")

        rng = random.Random(42)
        for typ in (
            "in_theme",
            "wrote",
            "part_of",
            "refers_to",
            "shared_entity",
            "lex_similar",
            "co_cited",
            "co_cited_eval",
            "work_refers_to",
            "work_shared_entity",
            "work_lex_similar",
        ):
            sample = list(by_type.get(typ) or [])
            rng.shuffle(sample)
            sample = sample[:15]
            lines.append(f"## Examples: `{typ}` ({len(by_type.get(typ) or [])} total, show {len(sample)})")
            lines.append("")
            if not sample:
                lines.append("_none_")
                lines.append("")
                continue
            for e in sample:
                src = e.get("source")
                tgt = e.get("target")
                src_l = doc_title.get(src, work_title.get(str(src).removeprefix("work:"), src))
                tgt_l = doc_title.get(tgt, work_title.get(str(tgt).removeprefix("work:"), tgt))
                lines.append(
                    f"- **{src_l}** → **{tgt_l}** · weight=`{e.get('weight')}` · provenance=`{e.get('provenance')}`"
                )
            lines.append("")

        report_path = out_dir / "graph_index_report.md"
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (out_dir / "stats.json").write_text(
            json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "index_fingerprint.txt").write_text(
            str(payload.get("fingerprint") or ""), encoding="utf-8"
        )
        print(f"REPORT {report_path}")
        print(
            f"STATS documents={stats.get('documents')} works={stats.get('works')} "
            f"edges={sum((stats.get('edge_counts_by_type') or {}).values())} "
            f"isolated_docs={stats.get('isolated_documents')} isolated_works={stats.get('isolated_works')}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
