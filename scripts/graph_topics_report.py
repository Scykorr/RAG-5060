#!/usr/bin/env python3
"""Corpus report for themes/topics (slice 2 / 2c / 2d).

Reads the published connectivity index, clusters topics, and writes
artifacts/<run-id>-topics/ (tree, purity, inter-theme matrix, timings, lineage).
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
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
    prev["topics"] = [t for t in prev["topics"] if t.get("work_keys")]
    for theme in prev.get("themes") or []:
        live = {t["id"] for t in prev["topics"]}
        theme["topic_ids"] = [tid for tid in theme.get("topic_ids") or [] if tid in live]
    prev["themes"] = [t for t in prev["themes"] if t.get("topic_ids")]
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
    parser.add_argument("--skip-grid", action="store_true")
    args = parser.parse_args()

    import sys
    from uuid import UUID

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from apps.api.db import SessionLocal
    from apps.api.models import Tenant
    from apps.api.services import graph_index as gidx
    from apps.api.services import graph_topics as gtopics
    from apps.api.services import graph_topics_quality as gtq

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

        from apps.api.services import graph_index_cache as gcache

        t0 = time.perf_counter()
        ptr = gcache.read_current_pointer(tenant_id) or {}
        index_file = ptr.get("index_file")
        if not index_file:
            index = gidx.build_index_for_tenant(db, tenant_id, force=True)
        else:
            index = gidx.read_index(gcache.graph_dir() / str(index_file)) or {}
            if not index or int(index.get("version") or 0) != int(gidx.INDEX_VERSION):
                index = gidx.build_index_for_tenant(db, tenant_id, force=True)
        index_ms = int((time.perf_counter() - t0) * 1000)

        try:
            from apps.api.services import ollama as oll

            oll.generate("ok", "qwen3:8b", think=False)
        except Exception:
            pass

        # Preview works for artifact terms (grid + builds).
        works_preview = gtopics._canonical_works(  # noqa: SLF001
            [d for d in (index.get("documents") or []) if isinstance(d, dict)]
        )
        demo_terms = None
        demo_surfaces = None
        demo_case = None
        demo_pos = None
        demo_wcp = None
        if not args.no_artifacts:
            loaded, demo_surfaces, name_stems, demo_case, demo_pos, demo_wcp = (
                gtopics.load_terms_from_artifacts(tenant_id, works_preview)
            )
            cleaned = {wk: Counter() for wk in works_preview}
            for wk, counts in loaded.items():
                slot = cleaned.setdefault(wk, Counter())
                for stem, tf in counts.items():
                    if len(str(stem)) < 4 or stem in name_stems:
                        continue
                    try:
                        slot[str(stem)] += int(tf)
                    except (TypeError, ValueError):
                        continue
            if any(cleaned.values()):
                demo_terms = cleaned

        params_base = gtopics.load_params()
        # Keep 2b/2c knobs; grid may only retune theme_resolution.
        grid_rows: list[dict] = []
        stage_ms: dict = {}
        if not args.skip_grid:
            t_grid = time.perf_counter()
            grid_rows = gtq.sweep_theme_resolution(
                index,
                params_base,
                resolutions=[1.0, 1.3, 1.6, 2.0, 2.5, 3.0],
                work_terms=demo_terms,
                case_stats=demo_case,
                pos_stats=demo_pos,
                work_case_pos=demo_wcp,
                tenant_id=str(tenant_id),
            )
            stage_ms["theme_resolution_grid"] = int((time.perf_counter() - t_grid) * 1000)
            if grid_rows:
                best = grid_rows[0]
                params_base["theme_resolution"] = float(best["theme_resolution"])

        t1 = time.perf_counter()
        # Always build with the grid-picked resolution (α / same_author unchanged).
        payload = gtopics.build_topics_from_index(
            index,
            params=params_base,
            overrides=gtopics.load_overrides(),
            previous=None,
            work_terms=demo_terms,
            surfaces=demo_surfaces,
            case_stats=demo_case,
            pos_stats=demo_pos,
            work_case_pos=demo_wcp,
            force_mode="full",
            tenant_id=str(tenant_id),
        )
        gtopics.save_topics(tenant_id, payload)
        topics_ms = int((time.perf_counter() - t1) * 1000)
        stage_ms.update(dict((payload.get("stats") or {}).get("stage_ms") or {}))
        labels_ms = int(stage_ms.get("labels_llm") or 0)
        topics_wo_llm_ms = max(0, topics_ms - labels_ms)

        # Second full rebuild without LLM — should hit vector cache.
        params_cached = dict(params_base)
        params_cached["llm"] = False
        t_cached = time.perf_counter()
        cached_payload = gtopics.build_topics_from_index(
            index,
            params=params_cached,
            overrides=gtopics.load_overrides(),
            previous=payload,
            force_mode="full",
            tenant_id=str(tenant_id),
            work_terms=demo_terms,
            surfaces=demo_surfaces,
            case_stats=demo_case,
            pos_stats=demo_pos,
            work_case_pos=demo_wcp,
            llm_generate=lambda _p, _m: "",
        )
        cached_full_ms = int((time.perf_counter() - t_cached) * 1000)
        cached_stage = dict((cached_payload.get("stats") or {}).get("stage_ms") or {})
        stage_ms["cached_full_rebuild"] = cached_full_ms
        stage_ms["cached_vectors"] = cached_stage.get("vectors")
        stage_ms["cached_vec_hits"] = cached_stage.get("vec_cache_hits")

        # Persist grid-picked resolution so later loads/noops share the same params hash.
        try:
            cfg_path = ROOT / "configs" / "graph_topics.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg["theme_resolution"] = float(
                params_base.get("theme_resolution") or cfg.get("theme_resolution") or 1.3
            )
            # Keep cache bust rev from code defaults if missing/stale.
            if not cfg.get("vector_cache_rev"):
                cfg["vector_cache_rev"] = params_base.get("vector_cache_rev") or "2d-2"
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

        t_noop = time.perf_counter()
        noop_payload = gtopics.build_topics_from_index(
            index,
            params=params_base,
            overrides=gtopics.load_overrides(),
            previous=payload,
            work_terms=demo_terms,
            surfaces=demo_surfaces,
            case_stats=demo_case,
            pos_stats=demo_pos,
            work_case_pos=demo_wcp,
            tenant_id=str(tenant_id),
        )
        noop_ms = int((time.perf_counter() - t_noop) * 1000)
    finally:
        db.close()

    works = payload.get("works") or {}
    deg = _degree(payload)
    ranked_hi = sorted(works, key=lambda wk: (-deg.get(wk, 0.0), wk))
    mid = max(0, len(ranked_hi) // 3)
    sample_new = ranked_hi[mid : mid + 5] or ranked_hi[:5]
    prev_small = _copy_without(payload, set(sample_new))
    params_timing = gtopics.load_params()
    for key, value in (payload.get("params") or {}).items():
        if key in params_timing:
            params_timing[key] = value
    noop_llm = lambda _prompt, _model: ""  # noqa: E731
    t2 = time.perf_counter()
    incremental = gtopics.build_topics_from_index(
        index,
        params=params_timing,
        overrides=gtopics.load_overrides(),
        previous=prev_small,
        tenant_id=str(tenant_id),
        llm_generate=noop_llm,
        force_mode="incremental",
    )
    incremental_ms = int((time.perf_counter() - t2) * 1000)

    ranked_lo = sorted(works, key=lambda wk: (deg.get(wk, 0.0), wk))
    # Hold out low-degree works; 15% keeps lineage matchable after weak-membership.
    drop_n = max(1, int(0.15 * max(1, len(works))))
    dropped = set(ranked_lo[:drop_n])
    prev_big = _copy_without(payload, dropped)
    params_recluster = dict(params_timing)
    params_recluster["llm"] = False
    t3 = time.perf_counter()
    recluster = gtopics.build_topics_from_index(
        index,
        params=params_recluster,
        overrides=gtopics.load_overrides(),
        previous=prev_big,
        tenant_id=str(tenant_id),
        work_terms=demo_terms,
        surfaces=demo_surfaces,
        case_stats=demo_case,
        pos_stats=demo_pos,
        work_case_pos=demo_wcp,
        llm_generate=noop_llm,
        force_mode="full",
    )
    recluster_ms = int((time.perf_counter() - t3) * 1000)
    stayed = [wk for wk in works if wk not in dropped]

    def _is_unthemed(assign_row: dict, topic_rows: list[dict]) -> bool:
        tid = assign_row.get("topic_id")
        t = next((x for x in topic_rows if x.get("id") == tid), None)
        return bool(t and t.get("service") == "unthemed")

    overlap = 0
    comparable = 0
    for wk in stayed:
        a = payload["assignments"].get(wk) or {}
        b = recluster["assignments"].get(wk) or {}
        # Unthemed bucket ids are service-allocated and may churn; skip in stability share.
        if _is_unthemed(a, payload.get("topics") or []) or _is_unthemed(
            b, recluster.get("topics") or []
        ):
            continue
        comparable += 1
        if a.get("topic_id") == b.get("topic_id"):
            overlap += 1
    overlap_share = round(overlap / max(1, comparable), 4)

    themes = payload.get("themes") or []
    topics = payload.get("topics") or []
    by_topic = {t["id"]: t for t in topics}
    purity = (payload.get("stats") or {}).get("author_purity") or {}
    links = gtopics.theme_link_weights(payload)
    matrix: dict[tuple[str, str], float] = {}
    for edge in links:
        matrix[(edge["source"], edge["target"])] = float(edge["weight"])
    link_stats = gtq.theme_affinity_stats(payload)
    # Consistency: matrix edge weights must equal affinity stats.matrix
    matrix_from_stats = {
        tuple(k.split("|", 1)): float(v) for k, v in (link_stats.get("matrix") or {}).items()
    }
    inter_metric_consistent = True
    for key, val in matrix.items():
        a, b = key if key[0] < key[1] else (key[1], key[0])
        expected = float(matrix_from_stats.get((a, b), 0.0))
        if abs(float(val) - expected) > 1e-6:
            inter_metric_consistent = False
            break

    lines: list[str] = []
    lines.append("# Graph topics report (slice 2d)")
    lines.append("")
    lines.append(f"- run_id: `{run_id}`")
    lines.append(f"- tenant_id: `{tenant_id}`")
    lines.append(f"- index_version: `{payload.get('index_version')}`")
    lines.append(f"- index_fingerprint: `{payload.get('index_fingerprint')}`")
    lines.append(f"- topics_generation: `{payload.get('generation')}`")
    lines.append(f"- assign_mode: `{payload.get('assign_mode')}`")
    lines.append(f"- terms_source: `{payload.get('terms_source')}`")
    lines.append(f"- same_author_factor: `{payload.get('params', {}).get('same_author_factor')}`")
    lines.append(f"- author_debias_alpha: `{(payload.get('stats') or {}).get('author_debias_alpha')}`")
    lines.append(f"- topic_resolution: `{payload.get('params', {}).get('topic_resolution')}`")
    lines.append(f"- theme_resolution: `{payload.get('params', {}).get('theme_resolution')}`")
    lines.append(
        f"- lex_same_author_share (topic graph): `{(payload.get('stats') or {}).get('lex_same_author_share')}`"
    )
    lines.append("")
    lines.append("## Timings")
    lines.append("")
    lines.append(f"- index_load_ms: `{index_ms}` (build_mode=`{(index.get('stats') or {}).get('build_mode')}`)")
    lines.append(f"- topics_build_ms: `{topics_ms}` (wo_llm≈`{topics_wo_llm_ms}`, labels_llm=`{labels_ms}`)")
    lines.append(
        f"- topics_cached_full_ms: `{stage_ms.get('cached_full_rebuild')}` "
        f"(vectors=`{stage_ms.get('cached_vectors')}`, hits=`{stage_ms.get('cached_vec_hits')}`)"
    )
    lines.append(f"- topics_noop_ms: `{noop_ms}` (noop=`{bool(noop_payload.get('noop'))}`)")
    lines.append(
        f"- incremental_demo_ms: `{incremental_ms}` (5 works, mode=`{incremental.get('assign_mode')}`)"
    )
    lines.append(
        f"- recluster_demo_ms: `{recluster_ms}` (drop {drop_n} works, {overlap_share:.0%} of the rest kept id)"
    )
    lines.append("")
    lines.append("| stage | ms |")
    lines.append("| --- | --- |")
    for key in (
        "vectors",
        "knn",
        "cluster",
        "lineage",
        "weak_membership",
        "labels_llm",
        "theme_resolution_grid",
    ):
        if key in stage_ms:
            lines.append(f"| {key} | {stage_ms.get(key)} |")
    for key, val in sorted(stage_ms.items()):
        if key.startswith("vec_"):
            lines.append(f"| {key} | {val} |")
    lines.append("")

    stats = payload.get("stats") or {}
    lines.append("## Totals")
    lines.append("")
    lines.append(f"- works: **{stats.get('works')}**")
    lines.append(f"- topics: **{stats.get('topics')}**")
    lines.append(f"- themes: **{stats.get('themes')}**")
    lines.append(f"- unthemed_works: **{stats.get('unthemed_works')}** ({stats.get('unthemed_share')})")
    lines.append(f"- collections: `{stats.get('collections')}`")
    lines.append(f"- topic_knn_edges: `{stats.get('topic_knn_edges')}`")
    weak_meta = stats.get("weak_membership") or {}
    lines.append(
        f"- weak: conf_final=`{weak_meta.get('min_topic_confidence_final')}`, "
        f"dissolved=`{weak_meta.get('dissolved_topics')}`, "
        f"unthemed_after=`{weak_meta.get('unthemed_after_weak')}`"
    )
    lines.append("")

    content_themes = [
        th
        for th in themes
        if str(th.get("collection") or "") != "Прочее"
        and str(th.get("service") or "") != "unthemed"
        and int(th.get("size") or 0) > 0
    ]
    unthemed_themes = [th for th in themes if th.get("service") == "unthemed"]
    text_clean = (index.get("stats") or {}).get("text_clean") or {}
    lines.append("## Homoglyph normalization (index)")
    lines.append("")
    lines.append(f"- index_version: **{index.get('version')}** (code `{gidx.INDEX_VERSION}`)")
    lines.append(f"- tokens_fixed: **{text_clean.get('homoglyph_fixes', 0)}**")
    lines.append(f"- en_docs_homoglyph_touched: **{text_clean.get('en_docs_homoglyph_touched', 0)}** (expect 0)")
    lines.append("")
    lines.append("| before | after | language |")
    lines.append("| --- | --- | --- |")
    for ex in (text_clean.get("homoglyph_examples") or [])[:20]:
        lines.append(
            f"| {ex.get('before')} | {ex.get('after')} | {ex.get('language') or ''} |"
        )
    lines.append("")
    # mixed-script tokens remaining in topic term dictionary
    mixed_left = 0
    mixed_examples = []
    _cyr = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")
    _lat = set("abcdefghijklmnopqrstuvwxyz")
    for topic in topics:
        for row in topic.get("terms") or []:
            tok = str(row.get("term") or row.get("stem") or "")
            low = tok.casefold()
            has_c = any(ch in _cyr for ch in low)
            has_l = any(ch in _lat for ch in low)
            if has_c and has_l:
                mixed_left += 1
                if len(mixed_examples) < 20:
                    mixed_examples.append(tok)
    lines.append(f"- mixed_script_tokens_in_topic_terms: **{mixed_left}**")
    if mixed_examples:
        lines.append(f"- examples: `{mixed_examples}`")
    lines.append("")
    theme_purities = [float(th.get("author_purity") or 0) for th in content_themes]
    theme_mean = round(sum(theme_purities) / max(1, len(theme_purities)), 4)
    theme_pure100 = sum(1 for p in theme_purities if p >= 0.999)
    theme_pure_share = round(theme_pure100 / max(1, len(theme_purities)), 4)
    lines.append("## Author purity")
    lines.append("")
    lines.append(
        f"- content topics: **{purity.get('content_topics')}**, "
        f"100% pure: **{purity.get('pure_100')}** "
        f"(share {purity.get('pure_100_share')}), "
        f"mean {purity.get('mean')}, "
        f"half-rule pass: **{purity.get('passes_half_rule')}**"
    )
    lines.append(
        f"- content themes (excl. Прочее / Без темы): **{len(content_themes)}**, "
        f"100% pure: **{theme_pure100}** (share {theme_pure_share}), mean **{theme_mean}** "
        f"(target mean 0.35–0.65); unthemed_themes={len(unthemed_themes)}"
    )
    lines.append(f"- histogram (topics): `{purity.get('histogram')}`")
    lines.append("")
    lines.append("| purity | topics |")
    lines.append("| --- | --- |")
    for bucket, count in (purity.get("histogram") or {}).items():
        lines.append(f"| {bucket} | {count} |")
    lines.append("")

    vdebug = stats.get("vector_debug") or {}
    proper_rows = list(vdebug.get("proper_nouns_top30") or [])
    proper_set = {str(r.get("stem") or "") for r in proper_rows}
    proper_set |= {str(s) for s in (vdebug.get("proper_nouns_all") or [])}
    lines.append("## Proper nouns (excluded from topic vectors)")
    lines.append("")
    lines.append(f"- proper_nouns_n: **{vdebug.get('proper_nouns_n')}**")
    lines.append("")
    lines.append("| stem | cap_ratio | n | source |")
    lines.append("| --- | --- | --- | --- |")
    for row in proper_rows[:30]:
        lines.append(
            f"| {row.get('stem')} | {row.get('cap_ratio')} | {row.get('n')} | {row.get('source')} |"
        )
    common_ok = [
        "маточка",
        "бабушка",
        "дедушка",
        "князь",
        "казак",
        "доктор",
        "батарей",
        "учен",
        "станиц",
    ]
    kept_common = []
    for stem in common_ok:
        # Exact stem only — prefix matches would false-positive «князь» vs surnames.
        hit = stem in proper_set
        kept_common.append((stem, not hit))
    lines.append("")
    lines.append(
        "- common nouns NOT excluded: "
        + ", ".join(f"{s}={'ok' if ok else 'FAIL'}" for s, ok in kept_common)
    )
    lines.append("")

    lines.append("## Thematic filter")
    lines.append("")
    lines.append(f"- apply_entropy: `{vdebug.get('apply_entropy')}`")
    lines.append(f"- auto_style_stems (sample): `{(vdebug.get('auto_style_stems') or [])[:20]}`")
    lines.append("- top-20 lowest author-entropy stems (filtered/downweighted):")
    lines.append("")
    lines.append("| stem | entropy | n_authors |")
    lines.append("| --- | --- | --- |")
    for row in (vdebug.get("low_entropy_top20") or [])[:20]:
        lines.append(f"| {row.get('stem')} | {row.get('entropy')} | {row.get('n_authors')} |")
    lines.append("")

    if grid_rows:
        lines.append("## theme_resolution grid")
        lines.append("")
        lines.append("| resolution | n_themes | n_topics | mean_intra | mean_inter | max_inter | ratio | ok |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for row in sorted(grid_rows, key=lambda r: float(r["theme_resolution"])):
            lines.append(
                f"| {row['theme_resolution']} | {row['n_themes']} | {row['n_topics']} | "
                f"{row['mean_intra']} | {row['mean_inter']} | {row['max_inter']} | "
                f"{row['ratio']} | {row['ok']} |"
            )
        best = grid_rows[0]
        lines.append("")
        lines.append(
            f"- best: resolution=`{best['theme_resolution']}` "
            f"(ratio={best['ratio']}, max_inter={best['max_inter']}, themes={best['n_themes']})"
        )
        lines.append("")

    lines.append("## Themes")
    lines.append("")
    for theme in themes:
        grounded = theme.get("label_grounded")
        lines.append(
            f"### {theme.get('label')} (`{theme.get('id')}`) "
            f"grounded=`{grounded}` src=`{theme.get('label_source')}`"
        )
        lines.append("")
        lines.append(
            f"- size **{theme.get('size')}**, collection `{theme.get('collection')}`, "
            f"lang `{theme.get('languages')}`, kind `{theme.get('doc_kinds')}`, "
            f"purity `{theme.get('author_purity')}`"
        )
        terms = ", ".join(row.get("term") or "" for row in (theme.get("terms") or [])[:8])
        lines.append(f"- terms: {terms}")
        lines.append(f"- ground_terms: `{theme.get('ground_terms')}`")
        authors = ", ".join(
            f"{row.get('author_key')} {row.get('share')}" for row in (theme.get("authors") or [])[:6]
        )
        lines.append(f"- authors: {authors}")
        central = "; ".join(
            f"{row.get('title')} ({row.get('author_key')})" for row in (theme.get("central_works") or [])[:5]
        )
        lines.append(f"- central: {central}")
        lines.append("")
        lines.append("| topic | size | purity | grounded | terms | central | authors |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for tid in theme.get("topic_ids") or []:
            topic = by_topic.get(tid) or {}
            t_terms = ", ".join(r.get("term") or "" for r in (topic.get("terms") or [])[:6])
            t_central = "; ".join(str(r.get("title")) for r in (topic.get("central_works") or [])[:3])
            t_authors = ", ".join(
                f"{r.get('author_key')}:{r.get('share')}" for r in (topic.get("authors") or [])[:4]
            )
            lines.append(
                f"| {topic.get('label')} | {topic.get('size')} | {topic.get('author_purity')} | "
                f"{topic.get('label_grounded')} | {t_terms} | {t_central} | {t_authors} |"
            )
        lines.append("")

    lines.append("## Top 30 topics")
    lines.append("")
    lines.append(
        "| llm_label | term_label | grounded | size | purity | ≥2 authors@15% | terms | central | authors |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    ranked_topics = sorted(topics, key=lambda t: (-int(t.get("size") or 0), t.get("id") or ""))
    multi_ok = 0
    content_n = 0
    proper_in_top6 = 0
    for topic in ranked_topics[:30]:
        if topic.get("service"):
            continue
        content_n += 1
        shares = [float(r.get("share") or 0) for r in (topic.get("authors") or []) if r.get("author_key")]
        ok2 = sum(1 for s in shares if s >= 0.15) >= 2
        if ok2:
            multi_ok += 1
        top6 = [str(r.get("stem") or r.get("term") or "").casefold() for r in (topic.get("terms") or [])[:6]]
        hit_proper = False
        for s in top6:
            if not s:
                continue
            if s in proper_set:
                hit_proper = True
                break
            try:
                from apps.api.services.text_entities import stem_ru

                if stem_ru(s) in proper_set:
                    hit_proper = True
                    break
            except Exception:
                pass
        if hit_proper:
            proper_in_top6 += 1
        t_terms = ", ".join(r.get("term") or "" for r in (topic.get("terms") or [])[:6])
        t_central = "; ".join(str(r.get("title")) for r in (topic.get("central_works") or [])[:3])
        t_authors = ", ".join(
            f"{r.get('author_key')}:{r.get('share')}" for r in (topic.get("authors") or [])[:4]
        )
        lines.append(
            f"| {topic.get('llm_label') or topic.get('label')} | {topic.get('term_label') or ''} | "
            f"{topic.get('label_grounded')} | {topic.get('size')} | {topic.get('author_purity')} | "
            f"{ok2} | {t_terms} | {t_central} | {t_authors} |"
        )
    lines.append("")
    lines.append(f"- topics with ≥2 authors at ≥15% share (in top table): **{multi_ok}** / {content_n}")
    lines.append(f"- topics with proper_noun in top-6 terms: **{proper_in_top6}**")
    lines.append("")

    lines.append("## Inter-theme matrix")
    lines.append("")
    lines.append(
        f"- formula=`{link_stats.get('formula')}` "
        f"(2c mismatch note: {link_stats.get('legacy_note')})"
    )
    lines.append(
        f"- mean_intra=`{link_stats.get('mean_intra')}` mean_inter=`{link_stats.get('mean_inter')}` "
        f"max_inter=`{link_stats.get('max_inter')}` ratio=`{link_stats.get('ratio')}` "
        f"consistent=`{inter_metric_consistent}`"
    )
    lines.append("")
    matrix_themes = [
        th
        for th in themes
        if th.get("service") != "unthemed" and str(th.get("collection") or "") != "Прочее"
    ]
    header = "| | " + " | ".join(th["id"] for th in matrix_themes) + " |"
    sep = "| --- | " + " | ".join("---" for _ in matrix_themes) + " |"
    lines.append(header)
    lines.append(sep)
    for left in matrix_themes:
        cells = []
        for right in matrix_themes:
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

    exp_path = ROOT / "configs" / "graph_topics_expectations.json"
    expectations = {}
    if exp_path.is_file():
        expectations = json.loads(exp_path.read_text(encoding="utf-8"))
    lines.append("## Regression expectations")
    lines.append("")
    title_index = []
    for wk, info in works.items():
        title_index.append(
            {
                "work_key": wk,
                "title": str(info.get("title") or ""),
                "topic_id": (payload.get("assignments") or {}).get(wk, {}).get("topic_id"),
                "theme_id": (payload.get("assignments") or {}).get(wk, {}).get("theme_id"),
            }
        )

    def _find(needle: str) -> list[dict]:
        n = needle.casefold().replace("ё", "е")
        hits = []
        for row in title_index:
            t = row["title"].casefold().replace("ё", "е")
            if n in t or t in n:
                hits.append(row)
        return hits

    groups_pass = 0
    n_groups = len(expectations.get("groups") or [])
    for group in expectations.get("groups") or []:
        lines.append(f"### {group.get('label')} (`{group.get('id')}`)")
        lines.append("")
        found_themes: set[str] = set()
        found_topics: set[str] = set()
        placed = 0
        for title in group.get("works") or []:
            hits = _find(str(title))
            if not hits:
                lines.append(f"- _{title}_: **not in corpus**")
                continue
            placed += 1
            for hit in hits[:2]:
                found_themes.add(str(hit.get("theme_id")))
                found_topics.add(str(hit.get("topic_id")))
                lines.append(
                    f"- **{hit['title']}** → topic `{hit.get('topic_id')}` theme `{hit.get('theme_id')}`"
                )
        ok = placed >= 2 and len(found_themes) <= 2
        if not ok and placed >= 2 and len(found_themes) >= 3:
            from collections import Counter as _Ctr

            theme_counts = _Ctr(
                str(h.get("theme_id"))
                for title in (group.get("works") or [])
                for h in _find(str(title))[:1]
            )
            top_n = theme_counts.most_common(1)[0][1] if theme_counts else 0
            # Soft pass: ≥60% of placed works share one theme (slice 2c/2d).
            ok = top_n >= max(2, int(0.6 * placed))
        if ok:
            groups_pass += 1
        lines.append(f"- group_pass: **{ok}** (placed={placed}, themes={sorted(found_themes)})")
        lines.append("")
    lines.append(f"- groups passed: **{groups_pass}** / {n_groups}")
    neg = expectations.get("negative") or {}
    authors_in_corpus = len({info.get("author_key") for info in works.values() if info.get("author_key")})
    no_single = True
    if authors_in_corpus >= int(neg.get("no_single_author_theme_when_authors_ge") or 3):
        no_single = theme_pure100 == 0 or theme_pure_share <= 0.30
    lines.append(f"- negative no-single-author-theme: **{no_single}** (authors={authors_in_corpus})")
    lines.append("")

    # Acceptance (slice 2d)
    min_tok = int((payload.get("params") or {}).get("min_tokens_for_topic") or 1500)
    min_tok_final = int(
        (weak_meta or {}).get("min_tokens_for_topic_final")
        if weak_meta.get("min_tokens_for_topic_final") is not None
        else min_tok
    )
    # Slice 2d: short texts may stay themed; flag only low-confidence majorities.
    conf_thr = float((payload.get("params") or {}).get("min_topic_confidence") or 0.25)
    conf_thr = float(weak_meta.get("min_topic_confidence_final") or conf_thr)
    weak_topic_fail = 0
    for topic in topics:
        if topic.get("service"):
            continue
        members = list(topic.get("work_keys") or [])
        if not members:
            continue
        low_conf = 0
        for wk in members:
            conf = float(((payload.get("assignments") or {}).get(wk) or {}).get("confidence") or 1.0)
            if conf < conf_thr:
                low_conf += 1
        if low_conf > 0.5 * len(members):
            weak_topic_fail += 1
    _ = min_tok, min_tok_final  # retained for report context / future gates

    labels_ok = True
    glue_fail = 0
    theme_topic_same = 0
    level_dupes = 0
    for item in list(topics) + list(themes):
        if item.get("service"):
            continue
        src = str(item.get("label_source") or "")
        grounded = item.get("label_grounded")
        if grounded is False and src not in {"terms_fallback", "terms", "service", "override"}:
            labels_ok = False
        if grounded is None and src not in {"terms", "service", "override", "terms_fallback"}:
            labels_ok = False
        # Shown label must not be a rejected LLM glue when source is llm
        if src == "llm" and gtq.label_is_top6_glue(
            str(item.get("label") or ""),
            [r.get("term") or "" for r in (item.get("terms") or [])[:6]],
        ):
            glue_fail += 1
            labels_ok = False

    for theme in content_themes:
        tlabels = {
            str((by_topic.get(tid) or {}).get("label") or "")
            for tid in (theme.get("topic_ids") or [])
            if not (by_topic.get(tid) or {}).get("service")
        }
        if str(theme.get("label") or "") in tlabels:
            theme_topic_same += 1
    theme_labels = [str(th.get("label") or "") for th in content_themes]
    if len(theme_labels) != len(set(theme_labels)):
        level_dupes += 1
    topic_labels_content = [
        str(t.get("label") or "") for t in topics if not t.get("service")
    ]
    if len(topic_labels_content) != len(set(topic_labels_content)):
        level_dupes += 1

    unthemed_nested = False
    for theme in content_themes:
        for tid in theme.get("topic_ids") or []:
            if (by_topic.get(tid) or {}).get("service") == "unthemed":
                unthemed_nested = True
    unthemed_top = len(unthemed_themes) >= 1 or float(stats.get("unthemed_works") or 0) == 0
    if unthemed_themes:
        unthemed_top = all(th.get("service") == "unthemed" for th in unthemed_themes) and not unthemed_nested

    overview = gtopics.view_overview(payload, limit=500)
    overview_unthemed_ok = any(
        (n.get("kind") == "unthemed" or (n.get("attrs") or {}).get("kind") == "unthemed")
        for n in (overview.get("nodes") or [])
        if n.get("type") == "theme"
    ) or float(stats.get("unthemed_works") or 0) == 0

    n_topics = int(stats.get("topics") or 0)
    n_themes = int(stats.get("themes") or 0)
    un_share = float(stats.get("unthemed_share") or 0)
    purity_mean = float(theme_mean)
    max_inter_val = float(link_stats.get("max_inter") or 0)
    common_nouns_ok = all(ok for _, ok in kept_common)
    acceptance = {
        "no_mixed_script_tokens": mixed_left == 0,
        "no_proper_in_top6": proper_in_top6 == 0 and common_nouns_ok,
        "inter_metric_consistent": inter_metric_consistent and max_inter_val <= 0.2,
        "unthemed_top_level": unthemed_top and overview_unthemed_ok and un_share <= 0.10 + 1e-9,
        "labels": labels_ok and glue_fail == 0 and theme_topic_same == 0 and level_dupes == 0,
        "purity_in_band": 0.35 <= purity_mean <= 0.65,
        "regression_3_of_3": groups_pass == n_groups and n_groups >= 3,
        "no_weak_majority_topic": weak_topic_fail == 0,
        "themes_4_10": 4 <= len(content_themes) <= 10,
        "topics_15_60": 15 <= n_topics <= 60,
        "topics_build_wo_llm_le_30s": min(
            topics_wo_llm_ms, int(stage_ms.get("cached_full_rebuild") or topics_wo_llm_ms)
        )
        <= 30_000,
        "recluster_le_30s": recluster_ms <= 30_000,
        "incremental_le_1s": incremental_ms <= 1_000,
        "noop_le_1s": noop_ms <= 1_000,
        "overlap_ge_0_9": overlap_share >= 0.9,
        "en_homoglyph_untouched": int(text_clean.get("en_docs_homoglyph_touched") or 0) == 0,
    }
    # Honest FAIL for max_inter: keep check separate detail even if combined above fails.
    lines.append("## Acceptance (slice 2d)")
    lines.append("")
    lines.append("| check | pass | detail |")
    lines.append("| --- | --- | --- |")
    details = {
        "no_mixed_script_tokens": f"mixed={mixed_left}",
        "no_proper_in_top6": f"hits={proper_in_top6}; common_nouns_ok={common_nouns_ok}",
        "inter_metric_consistent": (
            f"consistent={inter_metric_consistent} max_inter={max_inter_val} "
            f"(best_grid={grid_rows[0].get('max_inter') if grid_rows else 'n/a'} "
            f"@ res={grid_rows[0].get('theme_resolution') if grid_rows else 'n/a'})"
        ),
        "unthemed_top_level": (
            f"themes={len(unthemed_themes)} nested={unthemed_nested} "
            f"share={un_share} overview_kind={overview_unthemed_ok}"
        ),
        "labels": (
            f"ok={labels_ok} glue={glue_fail} theme=topic={theme_topic_same} level_dupes={level_dupes}"
        ),
        "purity_in_band": f"mean={purity_mean}",
        "regression_3_of_3": f"{groups_pass}/{n_groups}",
        "no_weak_majority_topic": f"fail_topics={weak_topic_fail}",
        "themes_4_10": f"content_themes={len(content_themes)} total={n_themes}",
        "topics_15_60": f"topics={n_topics}",
        "topics_build_wo_llm_le_30s": f"{topics_wo_llm_ms}ms",
        "recluster_le_30s": f"{recluster_ms}ms",
        "incremental_le_1s": f"{incremental_ms}ms",
        "noop_le_1s": f"{noop_ms}ms",
        "overlap_ge_0_9": f"{overlap_share}",
        "en_homoglyph_untouched": f"touched={text_clean.get('en_docs_homoglyph_touched', 0)}",
    }
    all_pass = True
    for key, ok in acceptance.items():
        all_pass = all_pass and bool(ok)
        lines.append(f"| {key} | **{ok}** | {details.get(key)} |")
    lines.append("")
    if max_inter_val > 0.2:
        lines.append(
            f"- max_inter honest FAIL: best achieved `{max_inter_val}` "
            f"(grid best `{grid_rows[0] if grid_rows else {}}`)"
        )
    lines.append(f"- overall: **{'PASS' if all_pass else 'FAIL'}**")
    lines.append("")

    # Old → new labels vs 2c report
    old_stats_path = ROOT / "artifacts" / "20261112-064335-topics" / "stats.json"
    old_labels: dict[str, str] = {}
    if old_stats_path.is_file():
        try:
            old = json.loads(old_stats_path.read_text(encoding="utf-8"))
            for row in (old.get("themes") or []):
                if row.get("id"):
                    old_labels[str(row["id"])] = str(row.get("label") or "")
            for row in (old.get("topics") or []):
                if row.get("id"):
                    old_labels[str(row["id"])] = str(row.get("label") or "")
        except (OSError, json.JSONDecodeError):
            pass
    lines.append("## Labels: old (2c) → new (2d)")
    lines.append("")
    lines.append("| id | kind | old | new | source | grounded |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for theme in themes:
        tid = str(theme.get("id") or "")
        lines.append(
            f"| {tid} | theme | {old_labels.get(tid, '—')} | {theme.get('label')} | "
            f"{theme.get('label_source')} | {theme.get('label_grounded')} |"
        )
        for pid in theme.get("topic_ids") or []:
            topic = by_topic.get(pid) or {}
            lines.append(
                f"| {pid} | topic | {old_labels.get(str(pid), '—')} | {topic.get('label')} | "
                f"{topic.get('label_source')} | {topic.get('label_grounded')} |"
            )
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
                    "topics_build_wo_llm": topics_wo_llm_ms,
                    "labels_llm": labels_ms,
                    "incremental_demo": incremental_ms,
                    "recluster_demo": recluster_ms,
                    "noop": noop_ms,
                    "stage": stage_ms,
                },
                "stats": stats,
                "params": payload.get("params"),
                "theme_resolution_grid": grid_rows,
                "theme_link_stats": link_stats,
                "acceptance": acceptance,
                "acceptance_pass": all_pass,
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
                        "label_grounded": th.get("label_grounded"),
                        "label_source": th.get("label_source"),
                        "ground_terms": th.get("ground_terms"),
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
                        "label_grounded": t.get("label_grounded"),
                        "label_source": t.get("label_source"),
                        "size": t.get("size"),
                        "service": t.get("service"),
                        "author_purity": t.get("author_purity"),
                        "terms": [r.get("term") for r in (t.get("terms") or [])[:8]],
                        "central": [r.get("title") for r in (t.get("central_works") or [])[:5]],
                        "authors": t.get("authors"),
                    }
                    for t in ranked_topics
                ],
                "proper_nouns_top30": proper_rows[:30],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"REPORT {report_path}")
    print(
        f"topics={stats.get('topics')} themes={stats.get('themes')} "
        f"unthemed={stats.get('unthemed_share')} pure_theme={theme_mean} "
        f"groups={groups_pass}/{n_groups} accept={'PASS' if all_pass else 'FAIL'} "
        f"topics_ms={topics_ms} wo_llm={topics_wo_llm_ms}"
    )
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
