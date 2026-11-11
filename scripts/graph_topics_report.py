#!/usr/bin/env python3
"""Corpus report for themes/topics (slice 2 step 7).

Reads the published connectivity index, clusters topics, and writes
artifacts/<run-id>-topics/ (tree, purity, inter-theme matrix, timings, lineage).
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _copy_without(payload: dict, drop: set[str]) -> dict:
    """Previous topics state with ``drop`` works treated as not yet assigned."""
    prev = json.loads(json.dumps(payload))
    prev["assignments"] = {
        wk: row for wk, row in (prev.get("assignments") or {}).items() if wk not in drop
    }
    for topic in prev.get("topics") or []:
        topic["work_keys"] = [wk for wk in topic.get("work_keys") or [] if wk not in drop]
    prev["topics"] = [t for t in prev.get("topics") or [] if t.get("work_keys")]
    for theme in prev.get("themes") or []:
        live = {t["id"] for t in prev["topics"]}
        theme["topic_ids"] = [tid for tid in theme.get("topic_ids") or [] if tid in live]
    prev["themes"] = [t for t in prev.get("themes") or [] if t.get("topic_ids")]
    prev["noop"] = False
    return prev


def _degree(payload: dict) -> dict[str, float]:
    deg: dict[str, float] = defaultdict(float)
    for edge in payload.get("work_edges") or []:
        for end in (edge["source"], edge["target"]):
            deg[str(end)[5:]] += float(edge.get("weight") or 0)
    return deg


def main() -> int:
    parser = argparse.ArgumentParser(description="Topics/themes corpus report")
    parser.add_argument("--tenant-id", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--no-artifacts", action="store_true")
    args = parser.parse_args()

    import sys
    from uuid import UUID

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from apps.api.db import SessionLocal
    from apps.api.models import Tenant
    from apps.api.services import graph_index as gidx
    from apps.api.services import graph_topics as gtopics

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-topics")
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

        t0 = time.perf_counter()
        index = gidx.build_index_for_tenant(db, tenant_id, force=False)
        index_ms = int((time.perf_counter() - t0) * 1000)

        t1 = time.perf_counter()
        payload = gtopics.build_after_index(
            tenant_id,
            index,
            force=True,
            use_artifacts=not args.no_artifacts,
        )
        topics_ms = int((time.perf_counter() - t1) * 1000)
    finally:
        db.close()

    works = payload.get("works") or {}
    deg = _degree(payload)
    ranked = sorted(works, key=lambda wk: (-deg.get(wk, 0.0), wk))
    sample_new = ranked[:5]
    prev_small = _copy_without(payload, set(sample_new))
    t2 = time.perf_counter()
    incremental = gtopics.build_topics_from_index(
        index,
        params=gtopics.load_params(),
        overrides=gtopics.load_overrides(),
        previous=prev_small,
        tenant_id=str(tenant_id),
    )
    incremental_ms = int((time.perf_counter() - t2) * 1000)

    drop_n = max(1, int(0.25 * max(1, len(works))))
    dropped = set(ranked[:drop_n])
    prev_big = _copy_without(payload, dropped)
    t3 = time.perf_counter()
    recluster = gtopics.build_topics_from_index(
        index,
        params=gtopics.load_params(),
        overrides=gtopics.load_overrides(),
        previous=prev_big,
        tenant_id=str(tenant_id),
    )
    recluster_ms = int((time.perf_counter() - t3) * 1000)
    stayed = [wk for wk in works if wk not in dropped]
    overlap = 0
    for wk in stayed:
        if payload["assignments"].get(wk, {}).get("topic_id") == recluster["assignments"].get(wk, {}).get("topic_id"):
            overlap += 1
    overlap_share = round(overlap / max(1, len(stayed)), 4)

    themes = payload.get("themes") or []
    topics = payload.get("topics") or []
    by_topic = {t["id"]: t for t in topics}
    purity = (payload.get("stats") or {}).get("author_purity") or {}
    links = gtopics.theme_link_weights(payload)
    matrix: dict[tuple[str, str], float] = {}
    for edge in links:
        matrix[(edge["source"], edge["target"])] = float(edge["weight"])

    lines: list[str] = []
    lines.append("# Graph topics report (slice 2)")
    lines.append("")
    lines.append(f"- run_id: `{run_id}`")
    lines.append(f"- tenant_id: `{tenant_id}`")
    lines.append(f"- index_version: `{payload.get('index_version')}`")
    lines.append(f"- index_fingerprint: `{payload.get('index_fingerprint')}`")
    lines.append(f"- topics_generation: `{payload.get('generation')}`")
    lines.append(f"- assign_mode: `{payload.get('assign_mode')}`")
    lines.append(f"- terms_source: `{payload.get('terms_source')}`")
    lines.append(f"- same_author_factor: `{payload.get('params', {}).get('same_author_factor')}`")
    lines.append(f"- topic_resolution: `{payload.get('params', {}).get('topic_resolution')}`")
    lines.append(f"- theme_resolution: `{payload.get('params', {}).get('theme_resolution')}`")
    lines.append("")
    lines.append("## Timings")
    lines.append("")
    lines.append(f"- index_load_ms: `{index_ms}` (build_mode=`{(index.get('stats') or {}).get('build_mode')}`)")
    lines.append(f"- topics_build_ms: `{topics_ms}`")
    lines.append(f"- incremental_demo_ms: `{incremental_ms}` (5 works)")
    lines.append(f"- recluster_demo_ms: `{recluster_ms}` (drop {drop_n} works, {overlap_share:.0%} of the rest kept id)")
    lines.append("")
    stats = payload.get("stats") or {}
    lines.append("## Totals")
    lines.append("")
    lines.append(f"- works: **{stats.get('works')}**")
    lines.append(f"- topics: **{stats.get('topics')}**")
    lines.append(f"- themes: **{stats.get('themes')}**")
    lines.append(f"- unthemed_works: **{stats.get('unthemed_works')}** ({stats.get('unthemed_share')})")
    lines.append(f"- collections: `{stats.get('collections')}`")
    lines.append("")
    lines.append("## Author purity")
    lines.append("")
    lines.append(
        f"- content topics: **{purity.get('content_topics')}**, "
        f"100% pure: **{purity.get('pure_100')}** "
        f"(share {purity.get('pure_100_share')}), "
        f"mean {purity.get('mean')}, "
        f"half-rule pass: **{purity.get('passes_half_rule')}**"
    )
    lines.append(f"- histogram: `{purity.get('histogram')}`")
    lines.append("")
    lines.append("| purity | topics |")
    lines.append("| --- | --- |")
    for bucket, count in (purity.get("histogram") or {}).items():
        lines.append(f"| {bucket} | {count} |")
    lines.append("")
    lines.append("## Themes")
    lines.append("")
    for theme in themes:
        lines.append(f"### {theme.get('label')} (`{theme.get('id')}`)")
        lines.append("")
        lines.append(
            f"- size **{theme.get('size')}**, collection `{theme.get('collection')}`, "
            f"lang `{theme.get('languages')}`, kind `{theme.get('doc_kinds')}`, "
            f"purity `{theme.get('author_purity')}`"
        )
        terms = ", ".join(row.get("term") or "" for row in (theme.get("terms") or [])[:8])
        lines.append(f"- terms: {terms}")
        authors = ", ".join(
            f"{row.get('author_key')} {row.get('share')}" for row in (theme.get("authors") or [])[:6]
        )
        lines.append(f"- authors: {authors}")
        central = "; ".join(
            f"{row.get('title')} ({row.get('author_key')})" for row in (theme.get("central_works") or [])[:5]
        )
        lines.append(f"- central: {central}")
        lines.append("")
        lines.append("| topic | size | purity | terms | central | authors |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for tid in theme.get("topic_ids") or []:
            topic = by_topic.get(tid) or {}
            t_terms = ", ".join(r.get("term") or "" for r in (topic.get("terms") or [])[:6])
            t_central = "; ".join(
                str(r.get("title")) for r in (topic.get("central_works") or [])[:3]
            )
            t_authors = ", ".join(
                f"{r.get('author_key')}:{r.get('share')}" for r in (topic.get("authors") or [])[:4]
            )
            lines.append(
                f"| {topic.get('label')} | {topic.get('size')} | {topic.get('author_purity')} | {t_terms} | {t_central} | {t_authors} |"
            )
        lines.append("")

    lines.append("## Top 30 topics")
    lines.append("")
    lines.append("| label | size | purity | terms | central | authors |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    ranked_topics = sorted(topics, key=lambda t: (-int(t.get("size") or 0), t.get("id") or ""))
    for topic in ranked_topics[:30]:
        if topic.get("service") == "unthemed":
            continue
        t_terms = ", ".join(r.get("term") or "" for r in (topic.get("terms") or [])[:6])
        t_central = "; ".join(str(r.get("title")) for r in (topic.get("central_works") or [])[:3])
        t_authors = ", ".join(
            f"{r.get('author_key')}:{r.get('share')}" for r in (topic.get("authors") or [])[:4]
        )
        lines.append(
            f"| {topic.get('label')} | {topic.get('size')} | {topic.get('author_purity')} | {t_terms} | {t_central} | {t_authors} |"
        )
    lines.append("")
    lines.append("## Inter-theme matrix")
    lines.append("")
    header = "| | " + " | ".join(th["id"] for th in themes) + " |"
    sep = "| --- | " + " | ".join("---" for _ in themes) + " |"
    lines.append(header)
    lines.append(sep)
    for left in themes:
        cells = []
        for right in themes:
            if left["id"] == right["id"]:
                cells.append("—")
                continue
            a, b = (left["id"], right["id"]) if left["id"] < right["id"] else (right["id"], left["id"])
            cells.append(f"{matrix.get((a, b), 0):.3f}")
        lines.append("| " + left["id"] + " | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Incremental assignment (5 works)")
    lines.append("")
    lines.append(f"- assign_mode: `{incremental.get('assign_mode')}`")
    lines.append(f"- lineage trigger: `{(incremental.get('lineage') or {}).get('trigger')}`")
    for wk in sample_new:
        info = works[wk]
        row = (incremental.get("assignments") or {}).get(wk) or {}
        topic = next((t for t in incremental.get("topics") or [] if t["id"] == row.get("topic_id")), {})
        lines.append(
            f"- **{info.get('title')}** ({info.get('author_key')}) → `{topic.get('label')}` "
            f"conf={row.get('confidence')} mode=`{(row.get('provenance') or {}).get('mode')}`"
        )
    lines.append("")
    lines.append("## Recluster lineage (25% held out, then matched)")
    lines.append("")
    lineage = recluster.get("lineage") or {}
    lines.append(f"- trigger: `{lineage.get('trigger')}`")
    lines.append(f"- assign_mode: `{recluster.get('assign_mode')}`")
    lines.append(f"- matched: {len(lineage.get('matched') or [])}")
    lines.append(f"- new: {len(lineage.get('new') or [])}")
    lines.append(f"- removed: {len(lineage.get('removed') or [])}")
    lines.append(f"- split: {len(lineage.get('split') or [])}")
    lines.append(f"- merge: {len(lineage.get('merge') or [])}")
    lines.append(f"- id overlap on works that were not held out: **{overlap_share}**")
    lines.append("")

    report_path = out_dir / "topics_report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stats_path = out_dir / "stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "index_version": payload.get("index_version"),
                "topics_generation": payload.get("generation"),
                "assign_mode": payload.get("assign_mode"),
                "terms_source": payload.get("terms_source"),
                "timings_ms": {
                    "index_load": index_ms,
                    "topics_build": topics_ms,
                    "incremental_demo": incremental_ms,
                    "recluster_demo": recluster_ms,
                },
                "stats": stats,
                "params": payload.get("params"),
                "lineage_recluster": {
                    "trigger": lineage.get("trigger"),
                    "matched": len(lineage.get("matched") or []),
                    "new": len(lineage.get("new") or []),
                    "overlap_share": overlap_share,
                },
                "themes": [
                    {
                        "id": th.get("id"),
                        "label": th.get("label"),
                        "size": th.get("size"),
                        "author_purity": th.get("author_purity"),
                        "terms": [r.get("term") for r in (th.get("terms") or [])[:8]],
                        "topic_ids": th.get("topic_ids"),
                    }
                    for th in themes
                ],
                "topics": [
                    {
                        "id": t.get("id"),
                        "theme_id": t.get("theme_id"),
                        "label": t.get("label"),
                        "size": t.get("size"),
                        "service": t.get("service"),
                        "author_purity": t.get("author_purity"),
                        "terms": [r.get("term") for r in (t.get("terms") or [])[:8]],
                        "central": [r.get("title") for r in (t.get("central_works") or [])[:5]],
                        "authors": t.get("authors"),
                    }
                    for t in ranked_topics
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"REPORT {report_path}")
    print(
        f"topics={stats.get('topics')} themes={stats.get('themes')} "
        f"unthemed={stats.get('unthemed_share')} pure100={purity.get('pure_100_share')} "
        f"pass={purity.get('passes_half_rule')} topics_ms={topics_ms}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
