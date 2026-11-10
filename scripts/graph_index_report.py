#!/usr/bin/env python3
"""Build connectivity index and write a human review report (slice 1c)."""

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

BASELINE_RUN = "20261110-123456-graph-index"

LEX_GOOD = [
    ("Севастополь в декабре месяце", "Рубка леса"),
    ("Воскресение", "Братья Карамазовы"),
    ("Два письма к М Ганди", "Письмо студенту о праве"),
    ("О безумии", "Патриотизм и правительство"),
    ("Борис Годунов", "Евгений Онегин"),
    ("Мёртвые души", "Мертвые души"),
]
LEX_BAD = [
    ("Накануне", "Анна Каренина"),
    ("Ревизор", "Анна Каренина"),
    ("Яков Пасынков", "Анна Каренина"),
    ("Арап Петра Великого", "Муму"),
]


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f))


def _load_baseline(artifacts: Path) -> dict | None:
    side = artifacts / BASELINE_RUN / "stats.json"
    if side.exists():
        try:
            return json.loads(side.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def _title_pair_found(edges: list[dict], docs: list[dict], a: str, b: str) -> bool:
    from apps.api.services.text_entities import norm_ru

    by_id = {d["document_id"]: d for d in docs}
    na, nb = norm_ru(a), norm_ru(b)

    def hit(title: str, needle: str) -> bool:
        t = norm_ru(title or "")
        return needle in t or t in needle

    for e in edges:
        if e.get("type") != "lex_similar":
            continue
        da = by_id.get(str(e.get("source"))) or {}
        db = by_id.get(str(e.get("target"))) or {}
        ta, tb = da.get("title") or "", db.get("title") or ""
        if (hit(ta, na) and hit(tb, nb)) or (hit(ta, nb) and hit(tb, na)):
            return True
        # also work-level edges
    for e in edges:
        if e.get("type") != "work_lex_similar":
            continue
        # provenance examples carry doc titles indirectly via work_key in source/target
        sa = str(e.get("source") or "")
        sb = str(e.get("target") or "")
        if (na.replace(" ", "_") in sa and nb.replace(" ", "_") in sb) or (
            nb.replace(" ", "_") in sa and na.replace(" ", "_") in sb
        ):
            return True
    return False


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
    from apps.api.services.text_entities import norm_ru

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-graph-index")
    out_dir = Path(args.out) if args.out else ROOT / "artifacts" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    prev = _load_baseline(ROOT / "artifacts")

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
        work_meta = {}
        for d in docs:
            wk = d.get("work_key")
            work_meta[wk] = {
                "label": f"{d.get('author_key') or '—'} / {d.get('title')}",
                "fragment_count": work_meta.get(wk, {}).get("fragment_count", 0) + 1,
                "author_key": d.get("author_key"),
                "title": d.get("title"),
            }

        deg = Counter()
        work_deg = Counter()
        by_type: dict[str, list[dict]] = defaultdict(list)
        phrase_hub = Counter()
        phrase_forms: dict[str, Counter] = defaultdict(Counter)
        entity_df = Counter()
        entity_kind = {}
        author_mention_works: Counter = Counter()
        mention_examples: list[dict] = []

        for e in edges:
            typ = e.get("type") or "?"
            by_type[typ].append(e)
            if typ in (
                "refers_to",
                "shared_entity",
                "lex_similar",
                "co_cited",
                "co_cited_eval",
                "mentions_author",
            ):
                deg[str(e.get("source"))] += 1
                if not str(e.get("target")).startswith("author:"):
                    deg[str(e.get("target"))] += 1
            if typ == "refers_to":
                prov = e.get("provenance") or {}
                ph = prov.get("phrase") or ""
                if ph:
                    phrase_hub[ph] += 1
                    phrase_forms[ph][prov.get("match_form") or "?"] += 1
            if typ == "shared_entity":
                prov = e.get("provenance") or {}
                kinds = prov.get("entity_kinds") or {}
                for ent in prov.get("entities") or []:
                    entity_df[ent] += 1
                    if ent in kinds:
                        entity_kind[ent] = kinds[ent]
            if typ == "work_mentions_author":
                author_mention_works[str(e.get("target"))] += 1
            if typ == "mentions_author" and len(mention_examples) < 10:
                mention_examples.append(e)
            if typ.startswith("work_") and typ != "work_meta":
                work_deg[str(e.get("source"))] += 1
                work_deg[str(e.get("target"))] += 1

        deg_vals = sorted(deg.values())
        lines: list[str] = []
        lines.append("# Graph connectivity index report (slice 1c)")
        lines.append("")
        lines.append(f"- run_id: `{run_id}`")
        lines.append(f"- baseline: `{BASELINE_RUN}`")
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
        lines.append(f"- author_source_counts: `{stats.get('author_source_counts')}`")
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

        tc = stats.get("text_clean") or {}
        lines.append("## Text clean")
        lines.append("")
        lines.append(f"- chars_removed: **{tc.get('chars_removed')}**")
        lines.append(f"- chunks_touched: **{tc.get('chunks_touched')}**")
        for ex in (tc.get("examples") or [])[:5]:
            lines.append(f"- before: `{ex.get('before', '')[:160]}`")
            lines.append(f"  after: `{ex.get('after', '')[:160]}`")
        lines.append("")

        layer_ms = stats.get("layer_ms") or {}
        lines.append("## Layer timings (ms)")
        lines.append("")
        if layer_ms:
            for k, v in layer_ms.items():
                lines.append(f"- `{k}`: **{v}**")
        else:
            lines.append("_none_")
        lines.append("")

        if prev:
            lines.append(f"## Before / after vs `{BASELINE_RUN}`")
            lines.append("")
            prev_ec = prev.get("edge_counts_by_type") or {}
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
            lines.append(
                f"- documents_without_author: {prev.get('documents_without_author')} → "
                f"**{stats.get('documents_without_author')}**"
            )
            lines.append(
                f"- works: {prev.get('works')} → **{stats.get('works')}**"
            )
            lines.append("")

        lines.append("## Edges by type")
        lines.append("")
        for typ, cnt in sorted((stats.get("edge_counts_by_type") or {}).items(), key=lambda x: -x[1]):
            lines.append(f"- `{typ}`: **{cnt}**")
        lines.append("")

        lines.append("## mentions_author")
        lines.append("")
        for auth, n in author_mention_works.most_common(20):
            lines.append(f"- {n} works mention `{auth}`")
        lines.append("- examples:")
        for e in mention_examples[:10]:
            lines.append(
                f"  - {doc_title.get(e.get('source'), e.get('source'))} → {e.get('target')} · "
                f"{(e.get('provenance') or {}).get('context', '')[:120]}"
            )
        lines.append("")

        lines.append("## Top-30 refers_to phrases (with match_form)")
        lines.append("")
        for ph, n in phrase_hub.most_common(30):
            forms = ", ".join(f"{k}={v}" for k, v in phrase_forms[ph].most_common())
            lines.append(f"- {n}× `{ph}` · {forms}")
        lines.append("")

        lines.append("## Top-30 shared_entity surfaces (kind + edge-df)")
        lines.append("")
        for ent, n in entity_df.most_common(30):
            lines.append(f"- {n}× `{ent}` · kind={entity_kind.get(ent, '?')}")
        lines.append("")

        lines.append("## Top-20 works by fragment_count")
        lines.append("")
        for row in stats.get("top_works_by_fragments") or []:
            lines.append(
                f"- frag={row.get('fragment_count')} — {row.get('author_key') or '—'} / "
                f"{row.get('title')} (`{row.get('work_key')}`)"
            )
        lines.append("")
        lines.append("## Top-20 works by connectivity degree (+ fragment_count)")
        lines.append("")
        for i, (wk, d) in enumerate(work_deg.most_common(20), 1):
            key = wk.removeprefix("work:")
            meta = work_meta.get(key) or {}
            lines.append(
                f"{i}. degree={d} frag={meta.get('fragment_count', '?')} — {meta.get('label', key)}"
            )
        lines.append("")

        lex_debug = stats.get("lex_debug") or {}
        lines.append("## lex_similar")
        lines.append("")
        lines.append(f"- threshold: **{lex_debug.get('threshold')}** (requested {lex_debug.get('threshold_requested')})")
        lines.append(f"- score_count: {lex_debug.get('score_count')}")
        lines.append(f"- score_min/max: {lex_debug.get('score_min')} / {lex_debug.get('score_max')}")
        hist = lex_debug.get("score_hist") or {}
        if hist:
            lines.append("- histogram (score→count):")
            for k, v in list(hist.items())[:25]:
                lines.append(f"  - {k}: {v}")
        lines.append("- near threshold:")
        for row in (lex_debug.get("near_threshold") or [])[:10]:
            lines.append(
                f"  - w={row.get('score')} {doc_title.get(row.get('a'), row.get('a'))} ↔ "
                f"{doc_title.get(row.get('b'), row.get('b'))} · {row.get('terms')}"
            )
        lines.append("- regression good pairs:")
        for a, b in LEX_GOOD:
            found = _title_pair_found(edges, docs, a, b)
            lines.append(f"  - {'found' if found else 'absent'}: {a} ↔ {b}")
        lines.append("- regression bad pairs:")
        for a, b in LEX_BAD:
            found = _title_pair_found(edges, docs, a, b)
            lines.append(f"  - {'found' if found else 'absent'}: {a} ↔ {b}")
        lines.append("")

        lines.append("## Merged works / author_source samples")
        lines.append("")
        # show titles that collapsed multiple filenames
        by_work: dict[str, list] = defaultdict(list)
        for d in docs:
            by_work[d.get("work_key")].append(d)
        merges = [(wk, lst) for wk, lst in by_work.items() if len(lst) >= 2]
        merges.sort(key=lambda x: -len(x[1]))
        for wk, lst in merges[:25]:
            titles = sorted({(x.get("filename") or "")[:60] for x in lst})
            lines.append(f"- `{wk}` ← {len(lst)} docs: {titles[:4]}")
        former = [d for d in docs if d.get("author_source") in ("ingest_header", "text_header", "work_merge")]
        lines.append(f"- docs with non-filename author_source: **{len(former)}**")
        for d in former[:20]:
            lines.append(
                f"  - {d.get('author_source')}: {d.get('author_key')} / {d.get('title')} · {d.get('filename')}"
            )
        lines.append("")

        rng = random.Random(42)
        for typ in (
            "mentions_author",
            "refers_to",
            "shared_entity",
            "lex_similar",
            "work_refers_to",
            "work_mentions_author",
            "work_lex_similar",
        ):
            sample = list(by_type.get(typ) or [])
            rng.shuffle(sample)
            sample = sample[:12]
            lines.append(f"## Examples: `{typ}` ({len(by_type.get(typ) or [])} total, show {len(sample)})")
            lines.append("")
            if not sample:
                lines.append("_none_")
                lines.append("")
                continue
            for e in sample:
                src = e.get("source")
                tgt = e.get("target")
                src_l = doc_title.get(src, (work_meta.get(str(src).removeprefix("work:")) or {}).get("label", src))
                tgt_l = doc_title.get(tgt, (work_meta.get(str(tgt).removeprefix("work:")) or {}).get("label", tgt))
                if str(tgt).startswith("author:"):
                    tgt_l = tgt
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
            f"built_ms={payload.get('built_ms')} "
            f"isolated_docs={stats.get('isolated_documents')}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
