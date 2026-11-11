"""Themes and topics on top of the connectivity index.

Hierarchy: theme → topic → work → document → chunk.
Filename prefixes (``classic_`` / «Прочее») are collections (``in_collection``).
The legacy ``theme`` / ``in_theme`` fields stay as aliases and are not clusters.

Offline, stdlib Louvain, no new dependencies. Structural edges (wrote, part_of,
in_theme / in_collection) do not enter clustering.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from apps.api.services import graph_index_cache as gcache
from apps.api.services import graph_louvain
from apps.api.services import graph_topic_labels as labels

_ROOT = Path(__file__).resolve().parents[3]
_PARAMS_PATH = _ROOT / "configs" / "graph_topics.json"
_OVERRIDES_PATH = _ROOT / "configs" / "graph_topics_overrides.json"

_CLUSTER_TYPES = ("work_lex_similar", "work_refers_to", "work_shared_entity", "contains")
_DIRECTED = {
    "wrote",
    "part_of",
    "has_topic",
    "has_work",
    "contains",
    "refers_to",
    "work_refers_to",
    "in_theme",
    "in_collection",
    "theme_link",
    "topic_link",
}

_DEFAULTS: dict[str, Any] = {
    "edge_type_weights": {
        "work_lex_similar": 0.45,
        "work_refers_to": 1.9,
        "work_shared_entity": 1.8,
        "contains": 0.3,
    },
    "same_author_factor": 0.6,
    "weak_factor": 0.5,
    "min_edge_weight": 0.02,
    "topic_resolution": 2.6,
    "theme_resolution": 1.0,
    "min_topic_size": 3,
    "recluster_threshold": 0.2,
    "assign_confidence_min": 0.34,
    "jaccard_match": 0.5,
    "seed": 42,
    "max_passes": 16,
    "max_levels": 6,
    "llm": False,
    "llm_model": "qwen3:8b",
    "node_limit_default": 1500,
    "node_limit_max": 3000,
    "label_terms": 3,
    "characteristic_terms": 10,
    "central_works": 5,
    "topics_schema": 1,
}


def collection_of(doc: dict[str, Any]) -> str:
    """Source collection (folder / filename prefix). ``theme`` is the legacy alias."""
    raw = doc.get("collection")
    if raw is None or raw == "":
        raw = doc.get("theme")
    text = str(raw or "").strip()
    return text or "Прочее"


def is_collection_edge(edge: dict[str, Any]) -> bool:
    return str(edge.get("type") or "") in {"in_theme", "in_collection"}


def load_params(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else _PARAMS_PATH
    data: dict[str, Any] = {}
    if p.is_file():
        loaded = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    cfg = dict(_DEFAULTS)
    for key, value in data.items():
        if str(key).startswith("_") or key == "edge_type_weights":
            continue
        cfg[key] = value
    weights = dict(_DEFAULTS["edge_type_weights"])
    extra = data.get("edge_type_weights") or {}
    if isinstance(extra, dict):
        weights.update(extra)
    cfg["edge_type_weights"] = {k: float(v) for k, v in weights.items()}
    for num_key in (
        "same_author_factor",
        "weak_factor",
        "min_edge_weight",
        "topic_resolution",
        "theme_resolution",
        "recluster_threshold",
        "assign_confidence_min",
        "jaccard_match",
    ):
        cfg[num_key] = float(cfg[num_key])
    for int_key in (
        "min_topic_size",
        "seed",
        "max_passes",
        "max_levels",
        "node_limit_default",
        "node_limit_max",
        "label_terms",
        "characteristic_terms",
        "central_works",
        "topics_schema",
    ):
        cfg[int_key] = int(cfg[int_key])
    cfg["llm"] = bool(cfg["llm"])
    cfg["llm_model"] = str(cfg["llm_model"])
    return cfg


def load_overrides(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else _OVERRIDES_PATH
    data: dict[str, Any] = {}
    if p.is_file():
        loaded = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    topics = data.get("topics") if isinstance(data.get("topics"), dict) else {}
    themes = data.get("themes") if isinstance(data.get("themes"), dict) else {}
    merges = data.get("merges") if isinstance(data.get("merges"), list) else []
    splits = data.get("splits") if isinstance(data.get("splits"), list) else []
    return {"topics": topics, "themes": themes, "merges": merges, "splits": splits}


def topics_params_hash(
    params: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> str:
    blob = {
        "params": params if params is not None else load_params(),
        "overrides": overrides if overrides is not None else load_overrides(),
    }
    raw = json.dumps(blob, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def etag_of(body: dict[str, Any]) -> str:
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return '"' + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20] + '"'


def make_edge_id(source: str, target: str, typ: str) -> str:
    a, b = str(source), str(target)
    if typ not in _DIRECTED and a > b:
        a, b = b, a
    digest = hashlib.sha1(f"{typ}|{a}|{b}".encode("utf-8")).hexdigest()[:16]
    return "e:" + digest


def topics_file(tenant_id: UUID | str) -> Path:
    return gcache.graph_dir() / "topics" / f"{tenant_id}.json"


def load_topics(tenant_id: UUID | str) -> dict[str, Any] | None:
    path = topics_file(tenant_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_topics(tenant_id: UUID | str, payload: dict[str, Any]) -> None:
    path = topics_file(tenant_id)
    gcache.write_json_atomic(path, payload)


def delete_topics(tenant_id: UUID | str) -> int:
    path = topics_file(tenant_id)
    removed = 0
    for candidate in (path, path.with_suffix(".json.tmp")):
        if candidate.exists():
            try:
                candidate.unlink()
                removed += 1
            except OSError:
                pass
    return removed


# ---------------------------------------------------------------------------
# Clustering graph
# ---------------------------------------------------------------------------


def _canonical_works(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if doc.get("canonical") is False:
            continue
        if doc.get("duplicate_of") or doc.get("contained_in"):
            continue
        if str(doc.get("relation") or "canonical") in {"duplicate", "contained_in"}:
            continue
        wk = str(doc.get("work_key") or "")
        if not wk:
            continue
        grouped[wk].append(doc)
    works: dict[str, dict[str, Any]] = {}
    for wk, group in grouped.items():
        group_sorted = sorted(
            group,
            key=lambda d: (
                d.get("fragment_no") is not None,
                len(str(d.get("title") or "")),
                str(d.get("document_id") or ""),
            ),
        )
        authors = [str(d.get("author_key")) for d in group if d.get("author_key")]
        langs = [str(d.get("language") or "unknown") for d in group]
        kinds = [str(d.get("doc_kind") or "unknown") for d in group]
        colls = [collection_of(d) for d in group]
        author_ru = ""
        for d in group_sorted:
            if d.get("author_ru"):
                author_ru = str(d.get("author_ru"))
                break
        works[wk] = {
            "work_key": wk,
            "title": str(group_sorted[0].get("title") or wk),
            "author_key": Counter(authors).most_common(1)[0][0] if authors else None,
            "author_ru": author_ru or None,
            "collection": Counter(colls).most_common(1)[0][0],
            "language": Counter(langs).most_common(1)[0][0],
            "doc_kind": Counter(kinds).most_common(1)[0][0],
            "document_ids": sorted(str(d.get("document_id")) for d in group if d.get("document_id")),
            "filename": str(group_sorted[0].get("filename") or ""),
        }
    return works


def _shared_ok(prov: dict[str, Any]) -> bool:
    bags: list[dict[str, Any]] = []
    strength = prov.get("entity_strength")
    if isinstance(strength, dict) and strength:
        bags.append(strength)
    for ex in prov.get("examples") or []:
        if not isinstance(ex, dict):
            continue
        st = (ex.get("provenance") or {}).get("entity_strength") or {}
        if isinstance(st, dict) and st:
            bags.append(st)
    if not bags:
        return True
    return any(v == "strong" for bag in bags for v in bag.values())


def _collect_terms(prov: dict[str, Any]) -> list[str]:
    """Lex terms live on the edge or, after work aggregation, inside examples."""
    found: list[str] = []
    blobs = [prov]
    for ex in prov.get("examples") or []:
        if isinstance(ex, dict):
            blobs.append(ex.get("provenance") or {})
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        for term in blob.get("terms") or []:
            text = str(term).strip()
            if len(text) >= 4 and text not in found:
                found.append(text)
            if len(found) >= 12:
                return found
    return found


def _slim_provenance(prov: dict[str, Any], *, parts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    keep: dict[str, Any] = {}
    for key in ("terms", "entities", "entity_strength", "weak", "relation", "same_author", "phrase"):
        if key in prov and prov[key] not in (None, "", [], {}):
            keep[key] = prov[key]
    if prov.get("context"):
        keep["context"] = str(prov["context"])[:240]
    chunk_ids: list[str] = []
    for cid in prov.get("chunk_ids") or []:
        chunk_ids.append(str(cid))
    examples = []
    for ex in (prov.get("examples") or [])[:2]:
        if isinstance(ex, dict):
            examples.append(
                {
                    "weight": ex.get("weight"),
                    "provenance": {
                        k: (ex.get("provenance") or {}).get(k)
                        for k in ("terms", "entities", "entity_strength", "phrase", "chunk_ids")
                        if (ex.get("provenance") or {}).get(k)
                    },
                }
            )
            for cid in ((ex.get("provenance") or {}).get("chunk_ids") or []):
                chunk_ids.append(str(cid))
    if examples:
        keep["examples"] = examples
    if chunk_ids:
        keep["chunk_ids"] = list(dict.fromkeys(chunk_ids))[:12]
    if parts:
        keep["parts"] = parts
    return keep


def _work_key_of(node: str, known: set[str]) -> str | None:
    text = str(node)
    if text.startswith("work:"):
        wk = text[5:]
        return wk if wk in known else None
    return text if text in known else None


def build_cluster_graph(
    index: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Canonical works and content edges used for Louvain.

    Weights are a normalized sum of lex / refers / strong shared-entity / contains.
    Same-author links are multiplied by ``same_author_factor``. Weak links by
    ``weak_factor``. Structural edges are ignored.
    """
    docs = [d for d in (index.get("documents") or []) if isinstance(d, dict)]
    works = _canonical_works(docs)
    known = set(works)
    by_doc = {str(d.get("document_id")): d for d in docs if d.get("document_id")}
    weights_cfg: dict[str, float] = params["edge_type_weights"]
    same_factor = float(params["same_author_factor"])
    weak_factor = float(params["weak_factor"])
    min_w = float(params["min_edge_weight"])

    raw: dict[tuple[str, str, str], float] = {}
    meta: dict[tuple[str, str, str], dict[str, Any]] = {}

    def _add(wa: str, wb: str, typ: str, raw_w: float, prov: dict[str, Any]) -> None:
        if wa == wb or wa not in known or wb not in known:
            return
        if wa > wb:
            wa, wb = wb, wa
        key = (wa, wb, typ)
        raw[key] = raw.get(key, 0.0) + float(raw_w)
        slot = meta.setdefault(key, {"weak": False, "provenance": {}})
        if prov.get("weak") or prov.get("weak") is True:
            slot["weak"] = True
        # keep the heavier provenance blob
        terms = _collect_terms(prov)
        if terms:
            merged = list(slot["provenance"].get("terms") or [])
            for term in terms:
                if term not in merged:
                    merged.append(term)
            slot["provenance"]["terms"] = merged[:12]
        if float(raw_w) >= float(slot["provenance"].get("_w") or 0):
            copied = dict(prov)
            copied["terms"] = list(slot["provenance"].get("terms") or terms)
            copied["_w"] = float(raw_w)
            slot["provenance"] = copied

    for edge in index.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        typ = str(edge.get("type") or "")
        prov = dict(edge.get("provenance") or {})
        if edge.get("weak"):
            prov["weak"] = True
        weight = float(edge.get("weight") or 0)
        if weight <= 0 or is_collection_edge(edge):
            continue
        if typ in {"wrote", "part_of", "work_meta", "work_mentions_author", "mentions_author"}:
            continue
        if typ == "contains":
            da = by_doc.get(str(edge.get("source")))
            db = by_doc.get(str(edge.get("target")))
            if not da or not db:
                continue
            wa = str(da.get("work_key") or "")
            wb = str(db.get("work_key") or "")
            _add(wa, wb, "contains", weight, prov)
            continue
        if typ not in _CLUSTER_TYPES:
            continue
        if typ == "work_shared_entity" and not _shared_ok(prov):
            continue
        wa = _work_key_of(str(edge.get("source")), known)
        wb = _work_key_of(str(edge.get("target")), known)
        if not wa or not wb:
            continue
        _add(wa, wb, typ, weight, prov)

    max_raw: dict[str, float] = {}
    for (wa, wb, typ), value in raw.items():
        max_raw[typ] = max(max_raw.get(typ, 0.0), value)

    fused: dict[tuple[str, str], dict[str, Any]] = {}
    work_edges: list[dict[str, Any]] = []
    for (wa, wb, typ), value in sorted(raw.items()):
        peak = max_raw.get(typ) or 1.0
        coeff = float(weights_cfg.get(typ) or 0)
        if coeff == 0:
            continue
        slot = meta[(wa, wb, typ)]
        weak = bool(slot["weak"])
        scaled = coeff * (value / peak)
        if weak:
            scaled *= weak_factor
        same = False
        ak_a = works[wa].get("author_key")
        ak_b = works[wb].get("author_key")
        if ak_a and ak_b and ak_a == ak_b:
            same = True
            scaled *= same_factor
        if scaled <= 0:
            continue
        prov = _slim_provenance(slot["provenance"])
        prov.pop("_w", None)
        prov["raw"] = round(value, 6)
        prov["norm_coeff"] = coeff
        prov["same_author"] = same
        prov["same_author_factor"] = same_factor if same else 1.0
        src, tgt = f"work:{wa}", f"work:{wb}"
        work_edges.append(
            {
                "id": make_edge_id(src, tgt, typ),
                "source": src,
                "target": tgt,
                "type": typ,
                "weight": round(scaled, 6),
                "weak": weak,
                "provenance": prov,
            }
        )
        bucket = fused.setdefault(
            (wa, wb),
            {"weight": 0.0, "weak_parts": 0, "parts": 0, "part_rows": []},
        )
        bucket["weight"] += scaled
        bucket["parts"] += 1
        if weak:
            bucket["weak_parts"] += 1
        bucket["part_rows"].append({"type": typ, "contribution": round(scaled, 6)})

    adj: dict[str, dict[str, float]] = {wk: {} for wk in works}
    for (wa, wb), bucket in fused.items():
        if bucket["weight"] < min_w:
            continue
        adj[wa][wb] = bucket["weight"]
        adj[wb][wa] = bucket["weight"]

    # Drop display edges that were filtered by the fused floor (both ends now unlinked
    # for that pair). Keep them if the pair survived.
    alive = {tuple(sorted((a, b))) for a, nbrs in adj.items() for b in nbrs}
    work_edges = [
        e
        for e in work_edges
        if tuple(sorted((e["source"][5:], e["target"][5:]))) in alive
    ]
    work_edges.sort(key=lambda e: (e["type"], e["source"], e["target"]))
    return {"works": works, "adj": adj, "work_edges": work_edges, "fused": fused}


def _degree0(works: dict[str, Any], adj: dict[str, dict[str, float]]) -> list[str]:
    return sorted(wk for wk in works if not adj.get(wk))


def _absorb_small(
    groups: dict[str, set[str]],
    adj: dict[str, dict[str, float]],
    min_size: int,
) -> dict[str, set[str]]:
    membership: dict[str, str] = {}
    for tid, members in groups.items():
        for wk in members:
            membership[wk] = tid
    changed = True
    guard = 0
    while changed and guard < 20:
        guard += 1
        changed = False
        small = sorted(
            (tid for tid, mem in groups.items() if 0 < len(mem) < min_size),
            key=lambda tid: (len(groups[tid]), tid),
        )
        for tid in small:
            if tid not in groups:
                continue
            best = None
            best_w = 0.0
            for wk in groups[tid]:
                for nb, w in adj.get(wk, {}).items():
                    other = membership.get(nb)
                    if not other or other == tid or other not in groups:
                        continue
                    if w > best_w + 1e-12:
                        best_w = w
                        best = other
            if best is None:
                continue
            for wk in groups[tid]:
                membership[wk] = best
            groups[best] |= groups[tid]
            del groups[tid]
            changed = True
    return groups


def _cluster_components(
    works: dict[str, Any],
    adj: dict[str, dict[str, float]],
    params: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return raw topics and raw themes (temp keys)."""
    isolated = _degree0(works, adj)
    connected = sorted(wk for wk in works if wk not in set(isolated))
    edges = []
    seen: set[tuple[str, str]] = set()
    for wa, nbrs in adj.items():
        if wa not in connected and wa not in adj:
            continue
        for wb, w in nbrs.items():
            if wa >= wb:
                continue
            if wa not in set(connected) or wb not in set(connected):
                continue
            seen.add((wa, wb))
            edges.append((wa, wb, w))
    del seen
    part: dict[str, int] = {}
    if connected:
        part = graph_louvain.louvain(
            connected,
            edges,
            resolution=float(params["topic_resolution"]),
            seed=int(params["seed"]),
            max_passes=int(params["max_passes"]),
            max_levels=int(params["max_levels"]),
        )
    groups: dict[str, set[str]] = defaultdict(set)
    for wk, cid in part.items():
        groups[f"tmp-topic:{cid}"].add(wk)
    groups = _absorb_small(groups, adj, int(params["min_topic_size"]))

    topics: list[dict[str, Any]] = []
    for key in sorted(groups, key=lambda k: (min(groups[k]), k)):
        members = groups[key]
        coll = Counter(works[w]["collection"] for w in sorted(members)).most_common(1)[0][0]
        topics.append(
            {
                "temp": key,
                "work_keys": set(members),
                "service": None,
                "collection": coll,
                "theme_temp": None,
            }
        )

    # Leftover small components with no outgoing link → «Разное» per collection.
    misc_by_coll: dict[str, set[str]] = defaultdict(set)
    kept: list[dict[str, Any]] = []
    for topic in topics:
        if topic["service"] is None and len(topic["work_keys"]) < int(params["min_topic_size"]):
            misc_by_coll[topic["collection"]].update(topic["work_keys"])
        else:
            kept.append(topic)
    topics = kept
    for coll in sorted(misc_by_coll):
        topics.append(
            {
                "temp": f"tmp-misc:{coll}",
                "work_keys": set(misc_by_coll[coll]),
                "service": "misc",
                "collection": coll,
                "theme_temp": None,
            }
        )

    content_topics = [t for t in topics if not t["service"]]
    topic_nodes = [t["temp"] for t in content_topics]
    topic_edges: list[tuple[str, str, float]] = []
    cross: dict[tuple[str, str], float] = defaultdict(float)
    member_of = {}
    for t in content_topics:
        for wk in t["work_keys"]:
            member_of[wk] = t["temp"]
    for wa, nbrs in adj.items():
        ta = member_of.get(wa)
        if not ta:
            continue
        for wb, w in nbrs.items():
            if wa >= wb:
                continue
            tb = member_of.get(wb)
            if not tb or ta == tb:
                continue
            a, b = (ta, tb) if ta < tb else (tb, ta)
            cross[(a, b)] += w
    size_of = {t["temp"]: max(1, len(t["work_keys"])) for t in content_topics}
    for (a, b), raw_sum in sorted(cross.items()):
        denom = math.sqrt(size_of[a] * size_of[b])
        topic_edges.append((a, b, raw_sum / denom if denom else raw_sum))

    theme_part: dict[str, int] = {}
    if topic_nodes:
        theme_part = graph_louvain.louvain(
            topic_nodes,
            topic_edges,
            resolution=float(params["theme_resolution"]),
            seed=int(params["seed"]),
            max_passes=int(params["max_passes"]),
            max_levels=int(params["max_levels"]),
        )
    theme_groups: dict[int, list[str]] = defaultdict(list)
    for temp, cid in theme_part.items():
        theme_groups[cid].append(temp)
    themes: list[dict[str, Any]] = []
    for cid in sorted(theme_groups, key=lambda c: min(theme_groups[c])):
        themes.append({"temp": f"tmp-theme:{cid}", "topic_temps": sorted(theme_groups[cid])})
    temp_theme = {temp: th["temp"] for th in themes for temp in th["topic_temps"]}
    for topic in content_topics:
        topic["theme_temp"] = temp_theme.get(topic["temp"])

    # Isolates → «Без темы» inside the dominant theme of their collection.
    iso_by_coll: dict[str, set[str]] = defaultdict(set)
    for wk in isolated:
        iso_by_coll[works[wk]["collection"]].add(wk)

    def _dominant(coll: str) -> str | None:
        scores: Counter[str] = Counter()
        for topic in topics:
            if topic["service"] == "unthemed":
                continue
            th = topic.get("theme_temp")
            if not th:
                continue
            for wk in topic["work_keys"]:
                if works[wk]["collection"] == coll:
                    scores[th] += 1
        if scores:
            return scores.most_common(1)[0][0]
        return None

    for coll in sorted(iso_by_coll):
        th = _dominant(coll)
        if th is None:
            th = f"tmp-theme:coll:{coll}"
            themes.append({"temp": th, "topic_temps": []})
        temp = f"tmp-unthemed:{coll}"
        topics.append(
            {
                "temp": temp,
                "work_keys": set(iso_by_coll[coll]),
                "service": "unthemed",
                "collection": coll,
                "theme_temp": th,
            }
        )
        for th_row in themes:
            if th_row["temp"] == th:
                th_row["topic_temps"].append(temp)

    # Park «Разное» on the dominant theme of that collection (or a new one).
    for topic in topics:
        if topic["service"] != "misc":
            continue
        th = _dominant(topic["collection"])
        if th is None:
            th = f"tmp-theme:coll:{topic['collection']}"
            themes.append({"temp": th, "topic_temps": []})
        topic["theme_temp"] = th
        for th_row in themes:
            if th_row["temp"] == th and topic["temp"] not in th_row["topic_temps"]:
                th_row["topic_temps"].append(topic["temp"])

    # Drop empty themes
    themes = [th for th in themes if th["topic_temps"]]
    return topics, themes


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _match_ids(
    clusters: list[tuple[str, set[str]]],
    previous: list[tuple[str, set[str]]],
    *,
    threshold: float,
    alloc: Callable[[], str],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Greedy Jaccard match. Returns temp→stable id and lineage fragment."""
    pairs: list[tuple[float, str, str]] = []
    for temp, members in clusters:
        for old_id, old_members in previous:
            score = _jaccard(members, old_members)
            if score + 1e-12 >= threshold:
                pairs.append((score, temp, old_id))
    pairs.sort(key=lambda row: (-row[0], row[1], row[2]))
    used_new: set[str] = set()
    used_old: set[str] = set()
    mapping: dict[str, str] = {}
    matched: list[dict[str, Any]] = []
    for score, temp, old_id in pairs:
        if temp in used_new or old_id in used_old:
            continue
        used_new.add(temp)
        used_old.add(old_id)
        mapping[temp] = old_id
        matched.append({"id": old_id, "temp": temp, "jaccard": round(score, 4)})
    # splits / merges from remaining high-jaccard pairs
    splits: list[dict[str, Any]] = []
    merges: list[dict[str, Any]] = []
    by_old: dict[str, list[tuple[float, str]]] = defaultdict(list)
    by_new: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for score, temp, old_id in pairs:
        by_old[old_id].append((score, temp))
        by_new[temp].append((score, old_id))
    for old_id, rows in by_old.items():
        extras = [(s, t) for s, t in rows if mapping.get(t) != old_id]
        if old_id in used_old and extras:
            splits.append(
                {
                    "from": old_id,
                    "into": [mapping.get(t) or t for _, t in extras],
                    "jaccard": round(extras[0][0], 4),
                }
            )
    for temp, rows in by_new.items():
        if temp not in mapping:
            continue
        extras = [(s, old) for s, old in rows if old != mapping[temp]]
        if extras:
            merges.append(
                {
                    "into": mapping[temp],
                    "from": [old for _, old in extras],
                    "jaccard": round(extras[0][0], 4),
                }
            )
    new_ids: list[str] = []
    for temp, _members in clusters:
        if temp not in mapping:
            fresh = alloc()
            mapping[temp] = fresh
            new_ids.append(fresh)
    removed = [old_id for old_id, _m in previous if old_id not in used_old]
    lineage = {"matched": matched, "new": new_ids, "removed": removed, "split": splits, "merge": merges}
    return mapping, lineage


def _apply_structural_overrides(
    topics: list[dict[str, Any]],
    overrides: dict[str, Any],
    alloc_topic: Callable[[], str],
) -> dict[str, Any]:
    extra_lineage: dict[str, list[Any]] = {"merge": [], "split": []}
    by_id = {t["id"]: t for t in topics}
    for rule in overrides.get("merges") or []:
        if not isinstance(rule, dict):
            continue
        into = str(rule.get("into") or "")
        sources = [str(s) for s in (rule.get("sources") or rule.get("from") or [])]
        if into not in by_id:
            continue
        moved = []
        for src in sources:
            if src == into or src not in by_id:
                continue
            by_id[into]["work_keys"] |= by_id[src]["work_keys"]
            moved.append(src)
            by_id[src]["work_keys"] = set()
        if moved:
            extra_lineage["merge"].append({"into": into, "from": moved, "manual": True})
    topics[:] = [t for t in topics if t["work_keys"]]
    by_id = {t["id"]: t for t in topics}
    for rule in overrides.get("splits") or []:
        if not isinstance(rule, dict):
            continue
        source = str(rule.get("source") or "")
        keys = {str(k) for k in (rule.get("work_keys") or [])}
        if source not in by_id or not keys:
            continue
        take = by_id[source]["work_keys"] & keys
        if not take:
            continue
        by_id[source]["work_keys"] -= take
        new_id = str(rule.get("id") or alloc_topic())
        topics.append(
            {
                "id": new_id,
                "theme_id": by_id[source].get("theme_id"),
                "theme_temp": by_id[source].get("theme_temp"),
                "work_keys": set(take),
                "service": None,
                "collection": by_id[source].get("collection"),
                "label_override": rule.get("label"),
            }
        )
        extra_lineage["split"].append({"from": source, "into": new_id, "manual": True})
    topics[:] = [t for t in topics if t["work_keys"]]
    # pin: pull listed topic's previous members back (already the id's current set
    # if the caller restored them before). Label pin is applied in decorate.
    return extra_lineage


def _ban_terms(works: dict[str, dict[str, Any]]) -> set[str]:
    ban: set[str] = set()
    for info in works.values():
        if info.get("author_key"):
            ban.add(str(info["author_key"]).casefold())
        if info.get("author_ru"):
            ban.add(str(info["author_ru"]).casefold())
    try:
        from apps.api.services.graph_lexicon import lang_frozenset

        for lang in ("ru", "en"):
            ban |= {str(x).casefold() for x in lang_frozenset(lang, "lex_stopwords")}
    except Exception:
        pass
    return ban


def _terms_from_edges(
    work_edges: list[dict[str, Any]],
    works: dict[str, dict[str, Any]],
) -> dict[str, Counter]:
    counts: dict[str, Counter] = {wk: Counter() for wk in works}
    for edge in work_edges:
        if edge.get("type") != "work_lex_similar":
            continue
        terms = _collect_terms(edge.get("provenance") or {})
        for endpoint in (edge["source"], edge["target"]):
            wk = endpoint[5:] if str(endpoint).startswith("work:") else ""
            if wk not in counts:
                continue
            for term in terms:
                stem = str(term).strip()
                if len(stem) >= 4:
                    counts[wk][stem] += 1
    return counts


def _author_rows(members: set[str], works: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    ru: dict[str, str] = {}
    missing = 0
    for wk in members:
        ak = works[wk].get("author_key")
        if not ak:
            missing += 1
            continue
        counts[str(ak)] += 1
        if works[wk].get("author_ru"):
            ru[str(ak)] = str(works[wk]["author_ru"])
    total = max(1, len(members))
    rows = [
        {
            "author_key": ak,
            "author_ru": ru.get(ak),
            "works": n,
            "share": round(n / total, 4),
        }
        for ak, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    if missing:
        rows.append(
            {
                "author_key": None,
                "author_ru": None,
                "works": missing,
                "share": round(missing / total, 4),
            }
        )
    return rows


def _purity(members: set[str], works: dict[str, dict[str, Any]]) -> float:
    if not members:
        return 0.0
    counts: Counter[str] = Counter()
    for wk in members:
        ak = works[wk].get("author_key") or f"__none__:{wk}"
        counts[str(ak)] += 1
    return max(counts.values()) / len(members)


def author_purity_stats(topics: list[dict[str, Any]]) -> dict[str, Any]:
    content = [t for t in topics if not t.get("service")]
    hist = {"1.00": 0, "0.80-0.99": 0, "0.50-0.79": 0, "<0.50": 0}
    rows = []
    for topic in content:
        purity = float(topic.get("author_purity") or 0)
        if purity >= 0.999:
            hist["1.00"] += 1
        elif purity >= 0.8:
            hist["0.80-0.99"] += 1
        elif purity >= 0.5:
            hist["0.50-0.79"] += 1
        else:
            hist["<0.50"] += 1
        top = (topic.get("authors") or [{}])[0]
        rows.append(
            {
                "id": topic.get("id"),
                "label": topic.get("label"),
                "size": topic.get("size"),
                "purity": round(purity, 4),
                "top_author": top.get("author_key"),
                "top_share": top.get("share"),
            }
        )
    n = len(content)
    pure = hist["1.00"]
    mean = round(sum(float(t.get("author_purity") or 0) for t in content) / n, 4) if n else 0.0
    return {
        "content_topics": n,
        "pure_100": pure,
        "pure_100_share": round(pure / n, 4) if n else 0.0,
        "passes_half_rule": pure * 2 <= n,
        "mean": mean,
        "histogram": hist,
        "service_topics": sum(1 for t in topics if t.get("service")),
        "rows": rows,
    }


def _centrality(members: set[str], adj: dict[str, dict[str, float]]) -> list[tuple[str, float]]:
    scores = []
    for wk in members:
        score = sum(w for nb, w in adj.get(wk, {}).items() if nb in members)
        scores.append((wk, score))
    scores.sort(key=lambda row: (-row[1], row[0]))
    return scores


def _provenance_rows(members: set[str], work_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: Counter[str] = Counter()
    member_ids = {f"work:{wk}" for wk in members}
    for edge in work_edges:
        if edge["source"] in member_ids and edge["target"] in member_ids:
            totals[str(edge["type"])] += float(edge.get("weight") or 0)
    grand = sum(totals.values()) or 1.0
    rows = [
        {"type": typ, "weight": round(w, 4), "share": round(w / grand, 4)}
        for typ, w in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return rows


def _density(members: set[str], adj: dict[str, dict[str, float]]) -> float:
    n = len(members)
    if n < 2:
        return 0.0
    edges = 0
    for wa in members:
        for wb in adj.get(wa, {}):
            if wb in members and wa < wb:
                edges += 1
    return round((2.0 * edges) / (n * (n - 1)), 4)


def _decorate(
    topics: list[dict[str, Any]],
    themes: list[dict[str, Any]],
    works: dict[str, dict[str, Any]],
    adj: dict[str, dict[str, float]],
    work_edges: list[dict[str, Any]],
    params: dict[str, Any],
    overrides: dict[str, Any],
    work_terms: dict[str, Counter],
    surfaces: dict[str, str],
    label_cache: dict[str, str],
    llm_generate: Callable[[str, str], str] | None,
) -> None:
    ban = _ban_terms(works)
    topic_members = {t["id"]: set(t["work_keys"]) for t in topics}
    # Per-language class counts (topic id → stem counts) using each work's language.
    by_lang: dict[str, dict[str, Counter]] = defaultdict(dict)
    for tid, members in topic_members.items():
        for wk in members:
            lang = str(works[wk].get("language") or "unknown")
            slot = by_lang[lang].setdefault(tid, Counter())
            slot.update(work_terms.get(wk) or {})
    term_maps: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for lang, classes in by_lang.items():
        term_maps[lang] = labels.ctfidf(
            classes,
            surfaces=surfaces,
            top_n=int(params["characteristic_terms"]),
            ban=ban,
            lang=lang,
        )
    topic_over = overrides.get("topics") or {}
    for topic in topics:
        members = set(topic["work_keys"])
        topic["size"] = len(members)
        topic["work_keys"] = sorted(members)
        topic["density"] = _density(members, adj)
        topic["authors"] = _author_rows(members, works)
        topic["author_purity"] = round(_purity(members, works), 4)
        ordered = sorted(members)
        lang_counts = Counter(str(works[w].get("language") or "unknown") for w in ordered)
        kind_counts = Counter(str(works[w].get("doc_kind") or "unknown") for w in ordered)
        topic["languages"] = dict(lang_counts)
        topic["doc_kinds"] = dict(kind_counts)
        maj_lang = lang_counts.most_common(1)[0][0] if lang_counts else "unknown"
        topic["lang"] = maj_lang
        topic["doc_kind"] = kind_counts.most_common(1)[0][0] if kind_counts else "unknown"
        terms_by_lang = {
            lang: (term_maps.get(lang) or {}).get(topic["id"], [])
            for lang in lang_counts
        }
        topic["terms_by_lang"] = terms_by_lang
        topic["terms"] = list(terms_by_lang.get(maj_lang) or [])
        if not topic["terms"]:
            for rows in terms_by_lang.values():
                if rows:
                    topic["terms"] = list(rows)
                    break
        central = _centrality(members, adj)[: int(params["central_works"])]
        topic["central_works"] = [
            {
                "work_key": wk,
                "title": works[wk]["title"],
                "author_key": works[wk].get("author_key"),
                "score": round(score, 4),
            }
            for wk, score in central
        ]
        topic["provenance"] = _provenance_rows(members, work_edges)
        topic["collection"] = (
            Counter(works[w]["collection"] for w in sorted(members)).most_common(1)[0][0]
            if members
            else topic.get("collection")
        )
        service = topic.get("service")
        if service == "unthemed":
            topic["label"] = "Без темы"
            topic["label_source"] = "service"
        elif service == "misc":
            topic["label"] = "Разное"
            topic["label_source"] = "service"
        else:
            term_names = [row["term"] for row in topic["terms"]]
            title = topic["central_works"][0]["title"] if topic["central_works"] else None
            topic["label"] = labels.default_label(
                term_names, title, n_terms=int(params["label_terms"])
            )
            topic["label_source"] = "terms"
            cache_key = hashlib.sha1(
                ("|".join(topic["work_keys"])).encode("utf-8")
            ).hexdigest()[:16]
            llm = labels.maybe_llm_label(
                enabled=bool(params.get("llm")),
                model=str(params.get("llm_model") or "qwen3:8b"),
                terms=term_names,
                titles=[c["title"] for c in topic["central_works"]],
                lang=maj_lang,
                cache_key=cache_key,
                cache=label_cache,
                generate=llm_generate,
            )
            if llm:
                topic["label"] = llm
                topic["label_source"] = "llm"
        spec = topic_over.get(topic["id"]) or {}
        if isinstance(spec, dict) and spec.get("label"):
            topic["label"] = str(spec["label"])
            topic["label_source"] = "override"
        if topic.get("label_override"):
            topic["label"] = str(topic["label_override"])
            topic["label_source"] = "override"

    # Theme aggregates
    theme_over = overrides.get("themes") or {}
    for theme in themes:
        member_topics = [t for t in topics if t.get("theme_id") == theme["id"]]
        theme["topic_ids"] = [t["id"] for t in member_topics]
        all_works: set[str] = set()
        for t in member_topics:
            all_works.update(t["work_keys"])
        theme["size"] = len(all_works)
        theme["density"] = _density(all_works, adj)
        theme["authors"] = _author_rows(all_works, works)
        theme["author_purity"] = round(_purity(all_works, works), 4)
        ordered_works = sorted(all_works)
        lang_counts = Counter(str(works[w].get("language") or "unknown") for w in ordered_works)
        kind_counts = Counter(str(works[w].get("doc_kind") or "unknown") for w in ordered_works)
        theme["languages"] = dict(lang_counts)
        theme["doc_kinds"] = dict(kind_counts)
        maj_lang = lang_counts.most_common(1)[0][0] if lang_counts else "unknown"
        theme["lang"] = maj_lang
        theme["doc_kind"] = kind_counts.most_common(1)[0][0] if kind_counts else "unknown"
        # Reuse summed topic term scores for the majority language.
        score_acc: dict[str, dict[str, Any]] = {}
        for t in member_topics:
            for row in t.get("terms") or []:
                stem = row["stem"]
                slot = score_acc.setdefault(
                    stem, {"stem": stem, "term": row["term"], "score": 0.0, "lang": row.get("lang") or ""}
                )
                slot["score"] += float(row.get("score") or 0)
        ranked = sorted(score_acc.values(), key=lambda r: (-r["score"], r["stem"]))
        for row in ranked:
            row["score"] = round(row["score"], 4)
        theme["terms"] = ranked[: int(params["characteristic_terms"])]
        central = _centrality(all_works, adj)[: int(params["central_works"])]
        theme["central_works"] = [
            {
                "work_key": wk,
                "title": works[wk]["title"],
                "author_key": works[wk].get("author_key"),
                "score": round(score, 4),
            }
            for wk, score in central
        ]
        theme["provenance"] = _provenance_rows(all_works, work_edges)
        theme["collection"] = (
            Counter(works[w]["collection"] for w in all_works).most_common(1)[0][0]
            if all_works
            else None
        )
        term_names = [row["term"] for row in theme["terms"]]
        title = theme["central_works"][0]["title"] if theme["central_works"] else None
        # Service-only theme (just «Без темы») keeps a collection-scoped label.
        only_service = member_topics and all(t.get("service") for t in member_topics)
        if only_service:
            theme["label"] = str(theme.get("collection") or "Прочее")
            theme["label_source"] = "service"
        else:
            theme["label"] = labels.default_label(term_names, title, n_terms=int(params["label_terms"]))
            theme["label_source"] = "terms"
        spec = theme_over.get(theme["id"]) or {}
        if isinstance(spec, dict) and spec.get("label"):
            theme["label"] = str(spec["label"])
            theme["label_source"] = "override"


def _freeze_topics(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frozen = []
    for topic in sorted(topics, key=lambda t: (-int(t.get("size") or len(t.get("work_keys") or [])), t["id"])):
        row = dict(topic)
        if isinstance(row.get("work_keys"), set):
            row["work_keys"] = sorted(row["work_keys"])
        row.pop("temp", None)
        row.pop("theme_temp", None)
        row.pop("label_override", None)
        frozen.append(row)
    return frozen


def build_topics_from_index(
    index: dict[str, Any],
    *,
    params: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None,
    work_terms: dict[str, Counter] | None = None,
    surfaces: dict[str, str] | None = None,
    force_mode: str | None = None,
    llm_generate: Callable[[str, str], str] | None = None,
    terms_source: str = "provenance",
    tenant_id: str = "",
) -> dict[str, Any]:
    """Cluster ``index`` into themes and topics. Pure: no disk, no network."""
    t0 = time.perf_counter()
    params = params or load_params()
    overrides = overrides or {"topics": {}, "themes": {}, "merges": [], "splits": []}
    graph = build_cluster_graph(index, params)
    works: dict[str, dict[str, Any]] = graph["works"]
    adj: dict[str, dict[str, float]] = graph["adj"]
    work_edges: list[dict[str, Any]] = graph["work_edges"]
    if work_terms is None:
        work_terms = _terms_from_edges(work_edges, works)
    surfaces = surfaces or {}
    label_cache = dict((previous or {}).get("label_cache") or {})
    ph = topics_params_hash(params, overrides)
    prev_ph = (previous or {}).get("topics_params_hash")
    old_assign = dict((previous or {}).get("assignments") or {})
    current = set(works)
    old_keys = set(old_assign)
    life = (index.get("stats") or {}).get("lifecycle") or {}
    changed: set[str] = set()
    doc_work = {}
    for info in works.values():
        for did in info.get("document_ids") or []:
            doc_work[did] = info["work_key"]
    for did in life.get("changed_ids") or []:
        wk = doc_work.get(str(did))
        if wk:
            changed.add(wk)
    added = current - old_keys
    removed = old_keys - current
    delta = added | removed | changed
    frac = len(delta) / max(1, len(current)) if current else 1.0
    mode = force_mode
    trigger = "full"
    if mode not in {"full", "incremental"}:
        if previous is None or prev_ph != ph:
            mode = "full"
            trigger = "initial" if previous is None else "params"
        elif frac > float(params["recluster_threshold"]):
            mode = "full"
            trigger = "threshold"
        else:
            mode = "incremental"
            trigger = "incremental"
    elif mode == "full":
        trigger = "full"

    seq = {
        "topic": int(((previous or {}).get("id_seq") or {}).get("topic") or 0),
        "theme": int(((previous or {}).get("id_seq") or {}).get("theme") or 0),
    }

    def alloc_topic() -> str:
        seq["topic"] += 1
        return f"topic:{seq['topic']}"

    def alloc_theme() -> str:
        seq["theme"] += 1
        return f"theme:{seq['theme']}"

    if mode == "incremental" and previous is not None:
        built = _incremental(
            previous=previous,
            works=works,
            adj=adj,
            work_edges=work_edges,
            added=added,
            removed=removed,
            changed=changed,
            params=params,
            alloc_topic=alloc_topic,
        )
        if built["mean_confidence"] + 1e-12 < float(params["assign_confidence_min"]) and built["n_assigned"]:
            mode = "full"
            trigger = "low_confidence"
        else:
            topics = built["topics"]
            themes = built["themes"]
            assignments = built["assignments"]
            lineage = {
                "trigger": "incremental",
                "matched": [{"id": t["id"]} for t in topics],
                "new": built["new_ids"],
                "removed": built["removed_ids"],
                "split": [],
                "merge": [],
            }
            _decorate(
                topics, themes, works, adj, work_edges, params, overrides, work_terms, surfaces, label_cache, llm_generate
            )
            return _package(
                index=index,
                tenant_id=tenant_id,
                params=params,
                ph=ph,
                topics=_freeze_topics(topics),
                themes=themes,
                works=works,
                assignments=assignments,
                work_edges=work_edges,
                lineage=lineage,
                seq=seq,
                label_cache=label_cache,
                previous=previous,
                assign_mode="incremental",
                terms_source=terms_source,
                built_ms=int((time.perf_counter() - t0) * 1000),
            )

    raw_topics, raw_themes = _cluster_components(works, adj, params)
    prev_topics = []
    if previous:
        for row in previous.get("topics") or []:
            prev_topics.append((str(row.get("id")), set(row.get("work_keys") or [])))
    topic_map, topic_lineage = _match_ids(
        [(t["temp"], set(t["work_keys"])) for t in raw_topics],
        prev_topics,
        threshold=float(params["jaccard_match"]),
        alloc=alloc_topic,
    )
    topics = []
    for raw in raw_topics:
        topics.append(
            {
                "id": topic_map[raw["temp"]],
                "theme_id": None,
                "work_keys": set(raw["work_keys"]),
                "service": raw.get("service"),
                "collection": raw.get("collection"),
                "theme_temp": raw.get("theme_temp"),
            }
        )
    # Pin: restore previous members of pinned topics when they still exist.
    pinned = {
        tid
        for tid, spec in (overrides.get("topics") or {}).items()
        if isinstance(spec, dict) and spec.get("pin")
    }
    if previous and pinned:
        prev_by_id = {str(r.get("id")): set(r.get("work_keys") or []) for r in previous.get("topics") or []}
        holder = {}
        for topic in topics:
            for wk in topic["work_keys"]:
                holder[wk] = topic["id"]
        for tid in pinned:
            if tid not in {t["id"] for t in topics}:
                continue
            target = next(t for t in topics if t["id"] == tid)
            for wk in prev_by_id.get(tid) or set():
                if wk not in works:
                    continue
                owner = holder.get(wk)
                if owner == tid:
                    continue
                if owner:
                    other = next(t for t in topics if t["id"] == owner)
                    other["work_keys"].discard(wk)
                target["work_keys"].add(wk)
                holder[wk] = tid
        topics = [t for t in topics if t["work_keys"]]

    extra = _apply_structural_overrides(topics, overrides, alloc_topic)
    topic_lineage["merge"] = list(topic_lineage.get("merge") or []) + extra["merge"]
    topic_lineage["split"] = list(topic_lineage.get("split") or []) + extra["split"]

    # Themes from temp groups, then match on stable topic ids.
    temp_to_theme: dict[str, str] = {}
    for raw in raw_themes:
        temp_to_theme[raw["temp"]] = raw["temp"]
    theme_members: dict[str, set[str]] = defaultdict(set)
    for topic in topics:
        th = topic.get("theme_temp")
        if th:
            theme_members[th].add(topic["id"])
    # Topics created by manual split inherit theme_temp from the source via theme_id later.
    for topic in topics:
        if topic.get("theme_temp"):
            continue
        if topic.get("theme_id"):
            continue
        # attach to the theme of the largest sibling collection
        theme_members.setdefault("tmp-theme:orphan", set()).add(topic["id"])
        topic["theme_temp"] = "tmp-theme:orphan"

    prev_themes = []
    if previous:
        for row in previous.get("themes") or []:
            prev_themes.append((str(row.get("id")), set(row.get("topic_ids") or [])))
    theme_clusters = [(th, set(ids)) for th, ids in theme_members.items() if ids]
    theme_map, theme_lineage = _match_ids(
        theme_clusters,
        prev_themes,
        threshold=float(params["jaccard_match"]),
        alloc=alloc_theme,
    )
    themes = []
    for th, ids in theme_clusters:
        themes.append({"id": theme_map[th], "temp": th, "topic_ids": sorted(ids)})
    for topic in topics:
        th = topic.get("theme_temp")
        if th and th in theme_map:
            topic["theme_id"] = theme_map[th]
        elif themes:
            topic["theme_id"] = themes[0]["id"]
    lineage = {
        "trigger": trigger,
        "matched": topic_lineage["matched"],
        "new": topic_lineage["new"],
        "removed": topic_lineage["removed"],
        "split": topic_lineage["split"],
        "merge": topic_lineage["merge"],
        "themes_matched": theme_lineage["matched"],
        "themes_new": theme_lineage["new"],
        "themes_removed": theme_lineage["removed"],
    }
    assignments = {}
    for topic in topics:
        for wk in topic["work_keys"]:
            assignments[wk] = {
                "topic_id": topic["id"],
                "theme_id": topic["theme_id"],
                "confidence": 1.0,
                "provenance": {"mode": "cluster"},
            }
    _decorate(
        topics, themes, works, adj, work_edges, params, overrides, work_terms, surfaces, label_cache, llm_generate
    )
    return _package(
        index=index,
        tenant_id=tenant_id,
        params=params,
        ph=ph,
        topics=_freeze_topics(topics),
        themes=themes,
        works=works,
        assignments=assignments,
        work_edges=work_edges,
        lineage=lineage,
        seq=seq,
        label_cache=label_cache,
        previous=previous,
        assign_mode="full",
        terms_source=terms_source,
        built_ms=int((time.perf_counter() - t0) * 1000),
    )


def _incremental(
    *,
    previous: dict[str, Any],
    works: dict[str, dict[str, Any]],
    adj: dict[str, dict[str, float]],
    work_edges: list[dict[str, Any]],
    added: set[str],
    removed: set[str],
    changed: set[str],
    params: dict[str, Any],
    alloc_topic: Callable[[], str],
) -> dict[str, Any]:
    topics = []
    for row in previous.get("topics") or []:
        keys = {wk for wk in (row.get("work_keys") or []) if wk in works and wk not in removed}
        topics.append(
            {
                "id": row["id"],
                "theme_id": row.get("theme_id"),
                "work_keys": keys,
                "service": row.get("service"),
                "collection": row.get("collection"),
                "label": row.get("label"),
                "label_source": row.get("label_source"),
            }
        )
    by_work_topic = {}
    for topic in topics:
        for wk in topic["work_keys"]:
            by_work_topic[wk] = topic["id"]
    pinned = set()
    # confidence over newly assigned works
    confidences: list[float] = []
    new_ids: list[str] = []
    assign_targets = sorted(added | changed)
    for wk in assign_targets:
        votes: Counter[str] = Counter()
        neighbor_rows = []
        for nb, w in sorted(adj.get(wk, {}).items(), key=lambda kv: (-kv[1], kv[0])):
            tid = by_work_topic.get(nb)
            if not tid:
                continue
            votes[tid] += w
            if len(neighbor_rows) < 12:
                neighbor_rows.append({"work_key": nb, "topic_id": tid, "weight": round(w, 4)})
        if wk in by_work_topic:
            owner = next(t for t in topics if t["id"] == by_work_topic[wk])
            owner["work_keys"].discard(wk)
            by_work_topic.pop(wk, None)
        if not votes:
            confidences.append(0.0)
            coll = works[wk]["collection"]
            host = next(
                (
                    t
                    for t in topics
                    if t.get("service") == "unthemed" and t.get("collection") == coll
                ),
                None,
            )
            if host is None:
                # theme: dominant among this collection, else first theme
                theme_id = None
                scores: Counter[str] = Counter()
                for t in topics:
                    if not t.get("theme_id"):
                        continue
                    for other in t["work_keys"]:
                        if works[other]["collection"] == coll:
                            scores[t["theme_id"]] += 1
                if scores:
                    theme_id = scores.most_common(1)[0][0]
                elif topics and topics[0].get("theme_id"):
                    theme_id = topics[0]["theme_id"]
                host = {
                    "id": alloc_topic(),
                    "theme_id": theme_id,
                    "work_keys": set(),
                    "service": "unthemed",
                    "collection": coll,
                }
                topics.append(host)
                new_ids.append(host["id"])
            host["work_keys"].add(wk)
            by_work_topic[wk] = host["id"]
            continue
        best, best_w = votes.most_common(1)[0]
        total = sum(votes.values()) or 1.0
        conf = best_w / total
        confidences.append(conf)
        host = next(t for t in topics if t["id"] == best)
        host["work_keys"].add(wk)
        by_work_topic[wk] = best
        # stash provenance on the work via assignments later
        works[wk]["_assign_prov"] = {
            "mode": "incremental",
            "neighbors": neighbor_rows,
            "confidence": round(conf, 4),
        }
    topics = [t for t in topics if t["work_keys"]]
    removed_ids = []
    live_ids = {t["id"] for t in topics}
    for row in previous.get("topics") or []:
        if row.get("id") not in live_ids:
            removed_ids.append(row["id"])
    themes = []
    for row in previous.get("themes") or []:
        tids = [tid for tid in (row.get("topic_ids") or []) if tid in live_ids]
        # also topics that still point at this theme
        pointed = [t["id"] for t in topics if t.get("theme_id") == row.get("id")]
        tids = sorted(set(tids) | set(pointed))
        if not tids:
            continue
        themes.append({"id": row["id"], "topic_ids": tids, "label": row.get("label")})
    known_themes = {th["id"] for th in themes}
    for topic in topics:
        if topic.get("theme_id") not in known_themes:
            if themes:
                topic["theme_id"] = themes[0]["id"]
                if topic["id"] not in themes[0]["topic_ids"]:
                    themes[0]["topic_ids"].append(topic["id"])
    assignments = {}
    for topic in topics:
        for wk in topic["work_keys"]:
            prov = works[wk].pop("_assign_prov", None) or {"mode": "incremental" if wk in assign_targets else "kept"}
            conf = float(prov.get("confidence") or (1.0 if wk not in assign_targets else 0.0))
            assignments[wk] = {
                "topic_id": topic["id"],
                "theme_id": topic["theme_id"],
                "confidence": round(conf if wk in assign_targets else 1.0, 4),
                "provenance": prov,
            }
    mean = sum(confidences) / len(confidences) if confidences else 1.0
    return {
        "topics": topics,
        "themes": themes,
        "assignments": assignments,
        "mean_confidence": mean,
        "n_assigned": len(confidences),
        "new_ids": new_ids,
        "removed_ids": removed_ids,
    }


def _package(
    *,
    index: dict[str, Any],
    tenant_id: str,
    params: dict[str, Any],
    ph: str,
    topics: list[dict[str, Any]],
    themes: list[dict[str, Any]],
    works: dict[str, dict[str, Any]],
    assignments: dict[str, Any],
    work_edges: list[dict[str, Any]],
    lineage: dict[str, Any],
    seq: dict[str, int],
    label_cache: dict[str, str],
    previous: dict[str, Any] | None,
    assign_mode: str,
    terms_source: str,
    built_ms: int,
) -> dict[str, Any]:
    # Ensure theme topic_ids reflect final topics and drop helper keys.
    live = {t["id"] for t in topics}
    theme_rows = []
    for theme in themes:
        row = {k: v for k, v in theme.items() if k != "temp"}
        pointed = [t["id"] for t in topics if t.get("theme_id") == theme.get("id")]
        row["topic_ids"] = sorted(set(row.get("topic_ids") or []) & live | set(pointed))
        if row["topic_ids"]:
            theme_rows.append(row)
    theme_rows.sort(key=lambda th: (-int(th.get("size") or 0), th["id"]))
    unthemed = sum(int(t.get("size") or 0) for t in topics if t.get("service") == "unthemed")
    n_works = len(works)
    collections: dict[str, dict[str, int]] = {}
    for info in works.values():
        slot = collections.setdefault(info["collection"], {"works": 0, "documents": 0})
        slot["works"] += 1
        slot["documents"] += len(info.get("document_ids") or [])
    purity = author_purity_stats(topics)
    for info in works.values():
        if isinstance(info, dict):
            info.pop("_assign_prov", None)
    index_stale = bool(index.get("stale") or (index.get("stats") or {}).get("stale"))
    public_params = {
        "same_author_factor": params["same_author_factor"],
        "weak_factor": params["weak_factor"],
        "topic_resolution": params["topic_resolution"],
        "theme_resolution": params["theme_resolution"],
        "min_topic_size": params["min_topic_size"],
        "recluster_threshold": params["recluster_threshold"],
        "edge_type_weights": params["edge_type_weights"],
        "seed": params["seed"],
        "llm": params["llm"],
    }
    return {
        "topics_schema": int(params["topics_schema"]),
        "generation": int((previous or {}).get("generation") or 0) + 1,
        "tenant_id": tenant_id,
        "index_version": index.get("version"),
        "index_fingerprint": index.get("fingerprint") if isinstance(index.get("fingerprint"), str) else None,
        "index_params_hash": index.get("params_hash"),
        "topics_params_hash": ph,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "built_ms": built_ms,
        "assign_mode": assign_mode,
        "stale": index_stale,
        "noop": False,
        "id_seq": seq,
        "lineage": lineage,
        "label_cache": label_cache,
        "terms_source": terms_source,
        "stats": {
            "works": n_works,
            "topics": len(topics),
            "themes": len(theme_rows),
            "unthemed_works": unthemed,
            "unthemed_share": round(unthemed / n_works, 4) if n_works else 0.0,
            "author_purity": purity,
            "same_author_factor": params["same_author_factor"],
            "terms_source": terms_source,
            "collections": [
                {"collection": name, **counts}
                for name, counts in sorted(collections.items())
            ],
        },
        "themes": theme_rows,
        "topics": topics,
        "works": works,
        "assignments": assignments,
        "work_edges": work_edges,
        "params": public_params,
    }


# ---------------------------------------------------------------------------
# Artifacts + tenant build
# ---------------------------------------------------------------------------


def _extract_json_value(text: str, key: str) -> Any:
    needle = f'"{key}"'
    start = 0
    while True:
        i = text.find(needle, start)
        if i < 0:
            return None
        colon = text.find(":", i + len(needle))
        if colon < 0:
            return None
        j = colon + 1
        while j < len(text) and text[j] in " \t\r\n":
            j += 1
        if j >= len(text):
            return None
        if text[j] == "{":
            depth = 0
            for k in range(j, len(text)):
                if text[k] == "{":
                    depth += 1
                elif text[k] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[j : k + 1])
                        except json.JSONDecodeError:
                            return None
            return None
        start = i + len(needle)


_TEXT_RE = __import__("re").compile(r'"text"\s*:\s*"((?:\\.|[^"\\])*)"')


def load_terms_from_artifacts(
    tenant_id: UUID | str,
    works: dict[str, dict[str, Any]],
) -> tuple[dict[str, Counter], dict[str, str], set[str]]:
    """Stem counts, display surfaces, and proper-name stems from doc artifacts.

    Name stems reuse the index case/position heuristic so character names do not
    become topic labels. Surfaces are the most frequent form of each stem.
    """
    counts: dict[str, Counter] = {wk: Counter() for wk in works}
    surface_counts: dict[str, Counter] = defaultdict(Counter)
    case_acc: dict[str, dict[str, int]] = defaultdict(lambda: {"upper": 0, "lower": 0})
    pos_acc: dict[str, dict[str, int]] = defaultdict(lambda: {"mid": 0, "sent_start": 0})
    doc_to_work: dict[str, str] = {}
    for wk, info in works.items():
        for did in info.get("document_ids") or []:
            doc_to_work[str(did)] = wk
    for did, wk in doc_to_work.items():
        path = gcache.doc_artifact_path(tenant_id, did)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        flat = _extract_json_value(text, "term_counts")
        if isinstance(flat, dict):
            for stem, tf in flat.items():
                try:
                    counts[wk][str(stem)] += int(tf)
                except (TypeError, ValueError):
                    continue
        for key, acc in (("case_stats", case_acc), ("pos_stats", pos_acc)):
            blob = _extract_json_value(text, key)
            if not isinstance(blob, dict):
                continue
            for form, stats in blob.items():
                if not isinstance(stats, dict):
                    continue
                slot = acc[str(form)]
                for stat, n in stats.items():
                    try:
                        slot[str(stat)] = slot.get(str(stat), 0) + int(n)
                    except (TypeError, ValueError):
                        continue
        taken = 0
        for match in _TEXT_RE.finditer(text):
            taken += 1
            if taken > 6:
                break
            try:
                raw = json.loads('"' + match.group(1) + '"')
            except json.JSONDecodeError:
                continue
            for tok in __import__("re").findall(r"[A-Za-zА-Яа-яЁё]{4,}", str(raw)):
                surf = tok.casefold().replace("ё", "е")
                if any("а" <= ch <= "я" for ch in surf):
                    from apps.api.services.text_entities import stem_ru

                    stem = stem_ru(surf)
                else:
                    stem = surf
                surface_counts[stem][surf] += 1
    surfaces = {stem: cnt.most_common(1)[0][0] for stem, cnt in surface_counts.items() if cnt}
    name_stems: set[str] = set()
    if case_acc:
        from apps.api.services.graph_index import collect_lex_name_exclusions

        stems, _bases = collect_lex_name_exclusions(dict(case_acc), dict(pos_acc))
        name_stems = {s for s in stems if s}
    return counts, surfaces, name_stems


def _merge_label_terms(
    works: dict[str, dict[str, Any]],
    provenance: dict[str, Counter],
    artifacts: dict[str, Counter],
    name_stems: set[str],
) -> dict[str, Counter]:
    """Linking vocabulary outranks raw frequency; proper names stay out of labels.

    Each work contributes at most a small artifact count per stem, so one long
    novel cannot drown the terms that actually connect the cluster. A stem seen
    on a lex edge is boosted so shared theme words stay ahead of residual names.
    """
    merged: dict[str, Counter] = {wk: Counter() for wk in works}
    keys = set(works) | set(provenance) | set(artifacts)
    for wk in keys:
        slot = merged.setdefault(wk, Counter())
        for stem, tf in (artifacts.get(wk) or {}).items():
            text = str(stem)
            if len(text) < 4 or text in name_stems:
                continue
            try:
                slot[text] += min(int(tf), 6)
            except (TypeError, ValueError):
                continue
        for stem, n in (provenance.get(wk) or {}).items():
            text = str(stem)
            if len(text) < 4 or text in name_stems:
                continue
            slot[text] += int(n) * 40
    return merged


def build_after_index(
    tenant_id: UUID | str,
    index: dict[str, Any],
    *,
    force: bool = False,
    use_artifacts: bool = False,
    lock: bool = False,
    params: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
    force_mode: str | None = None,
    llm_generate: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """Build or reuse topics for a published connectivity index."""
    if lock and not gcache.acquire_build_lock(tenant_id):
        current = load_topics(tenant_id) or {}
        current = dict(current)
        current["status"] = "already_running"
        return current
    try:
        params = params or load_params()
        overrides = overrides or load_overrides()
        ph = topics_params_hash(params, overrides)
        previous = load_topics(tenant_id)
        fp = index.get("fingerprint") if isinstance(index.get("fingerprint"), str) else None
        if (
            not force
            and force_mode is None
            and previous
            and previous.get("index_fingerprint") == fp
            and previous.get("index_version") == index.get("version")
            and previous.get("index_params_hash") == index.get("params_hash")
            and previous.get("topics_params_hash") == ph
            and not previous.get("stale")
            and not (index.get("stale") or (index.get("stats") or {}).get("stale"))
        ):
            reused = dict(previous)
            reused["noop"] = True
            reused["built_ms"] = 0
            return reused
        work_terms = None
        surfaces = None
        terms_source = "provenance"
        if use_artifacts:
            preview = _canonical_works(
                [d for d in (index.get("documents") or []) if isinstance(d, dict)]
            )
            loaded, surfaces, name_stems = load_terms_from_artifacts(tenant_id, preview)
            if name_stems and any(loaded[w] for w in loaded):
                graph = build_cluster_graph(index, params)
                provenance = _terms_from_edges(graph["work_edges"], graph["works"])
                work_terms = _merge_label_terms(graph["works"], provenance, loaded, name_stems)
                terms_source = "mixed"
            elif any(loaded[w] for w in loaded):
                # Name heuristic missing: keep edge terms so character names
                # from raw counts cannot become the label.
                surfaces = surfaces or {}
                terms_source = "provenance"
        payload = build_topics_from_index(
            index,
            params=params,
            overrides=overrides,
            previous=previous,
            work_terms=work_terms,
            surfaces=surfaces,
            force_mode=force_mode,
            llm_generate=llm_generate,
            terms_source=terms_source,
            tenant_id=str(tenant_id),
        )
        save_topics(tenant_id, payload)
        return payload
    finally:
        if lock:
            gcache.release_build_lock(tenant_id)


def summary_of(payload: dict[str, Any]) -> dict[str, Any]:
    stats = payload.get("stats") or {}
    return {
        "assign_mode": payload.get("assign_mode"),
        "topics": stats.get("topics"),
        "themes": stats.get("themes"),
        "works": stats.get("works"),
        "noop": bool(payload.get("noop")),
        "built_ms": payload.get("built_ms"),
        "stale": bool(payload.get("stale")),
        "author_purity": stats.get("author_purity"),
        "unthemed_share": stats.get("unthemed_share"),
        "generation": payload.get("generation"),
        "status": payload.get("status"),
    }


# ---------------------------------------------------------------------------
# Level views
# ---------------------------------------------------------------------------


def node_limit(params: dict[str, Any], requested: int | None) -> int:
    default = int(params.get("node_limit_default") or 1500)
    hard = int(params.get("node_limit_max") or 3000)
    if requested is None:
        return default
    return max(1, min(hard, int(requested)))


def _node(
    nid: str,
    typ: str,
    label: str,
    size: float,
    *,
    theme_id: str | None = None,
    topic_id: str | None = None,
    collection: str | None = None,
    lang: str | None = None,
    doc_kind: str | None = None,
    attrs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": nid,
        "type": typ,
        "label": label,
        "size": size,
        "theme_id": theme_id,
        "topic_id": topic_id,
        "collection": collection,
        "lang": lang,
        "doc_kind": doc_kind,
        "attrs": attrs or {},
    }


def _cut(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    limit: int,
    *,
    seed: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, int]:
    total = len(nodes)
    if total <= limit:
        return nodes, edges, False, total

    def rank(node: dict[str, Any]) -> tuple:
        bonus = 1 if seed and node["id"] == seed else 0
        degree = float((node.get("attrs") or {}).get("degree") or 0)
        return (-bonus, -float(node.get("size") or 0), -degree, node["id"])

    kept = sorted(nodes, key=rank)[:limit]
    ids = {n["id"] for n in kept}
    kept_edges = [e for e in edges if e["source"] in ids and e["target"] in ids]
    return kept, kept_edges, True, total


def _meta(payload: dict[str, Any], *, level: str, truncated: bool, total: int, limit: int) -> dict[str, Any]:
    return {
        "level": level,
        "truncated": truncated,
        "total_nodes": total,
        "limit": limit,
        "index_version": payload.get("index_version"),
        "topics_version": payload.get("generation"),
        "stale": bool(payload.get("stale")),
        "assign_mode": payload.get("assign_mode"),
    }


def _envelope(
    payload: dict[str, Any],
    *,
    level: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    limit: int,
    seed: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # degree for ranking
    deg: Counter[str] = Counter()
    for edge in edges:
        deg[edge["source"]] += 1
        deg[edge["target"]] += 1
    for node in nodes:
        node.setdefault("attrs", {})
        node["attrs"]["degree"] = int(deg[node["id"]])
    nodes, edges, truncated, total = _cut(nodes, edges, limit, seed=seed)
    body = {
        "nodes": nodes,
        "edges": edges,
        "meta": _meta(payload, level=level, truncated=truncated, total=total, limit=limit),
    }
    if extra:
        body.update(extra)
    return body


def _topic_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in payload.get("topics") or []}


def _theme_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in payload.get("themes") or []}


def _link_edges(
    pairs: dict[tuple[str, str], float],
    typ: str,
) -> list[dict[str, Any]]:
    edges = []
    for (a, b), weight in sorted(pairs.items()):
        edges.append(
            {
                "id": make_edge_id(a, b, typ),
                "source": a,
                "target": b,
                "type": typ,
                "weight": round(weight, 6),
                "weak": False,
                "provenance": {"agg": "sum_cross_over_sqrt_size"},
            }
        )
    return edges


def theme_link_weights(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Inter-theme weights: sum of cross edges / sqrt(sizes)."""
    assign = payload.get("assignments") or {}
    themes = _theme_by_id(payload)
    size = {tid: max(1, int((themes.get(tid) or {}).get("size") or 1)) for tid in themes}
    cross: dict[tuple[str, str], float] = defaultdict(float)
    for edge in payload.get("work_edges") or []:
        wa = str(edge["source"])[5:]
        wb = str(edge["target"])[5:]
        ta = (assign.get(wa) or {}).get("theme_id")
        tb = (assign.get(wb) or {}).get("theme_id")
        if not ta or not tb or ta == tb:
            continue
        a, b = (ta, tb) if ta < tb else (tb, ta)
        cross[(a, b)] += float(edge.get("weight") or 0)
    weighted = {}
    for (a, b), raw in cross.items():
        denom = math.sqrt(size.get(a, 1) * size.get(b, 1))
        weighted[(a, b)] = raw / denom if denom else raw
    return _link_edges(weighted, "theme_link")


def _topic_links(payload: dict[str, Any], *, only: set[str] | None = None) -> list[dict[str, Any]]:
    assign = payload.get("assignments") or {}
    topics = _topic_by_id(payload)
    size = {tid: max(1, int((topics.get(tid) or {}).get("size") or 1)) for tid in topics}
    cross: dict[tuple[str, str], float] = defaultdict(float)
    for edge in payload.get("work_edges") or []:
        wa = str(edge["source"])[5:]
        wb = str(edge["target"])[5:]
        ta = (assign.get(wa) or {}).get("topic_id")
        tb = (assign.get(wb) or {}).get("topic_id")
        if not ta or not tb or ta == tb:
            continue
        if only is not None and ta not in only and tb not in only:
            continue
        a, b = (ta, tb) if ta < tb else (tb, ta)
        cross[(a, b)] += float(edge.get("weight") or 0)
    weighted = {}
    for (a, b), raw in cross.items():
        denom = math.sqrt(size.get(a, 1) * size.get(b, 1))
        weighted[(a, b)] = raw / denom if denom else raw
    return _link_edges(weighted, "topic_link")


def _theme_node(theme: dict[str, Any]) -> dict[str, Any]:
    return _node(
        theme["id"],
        "theme",
        str(theme.get("label") or theme["id"]),
        float(theme.get("size") or 0),
        theme_id=theme["id"],
        collection=theme.get("collection"),
        lang=theme.get("lang"),
        doc_kind=theme.get("doc_kind"),
        attrs={
            "terms": theme.get("terms") or [],
            "authors": theme.get("authors") or [],
            "density": theme.get("density"),
            "author_purity": theme.get("author_purity"),
            "provenance": theme.get("provenance") or [],
            "topic_ids": theme.get("topic_ids") or [],
            "central_works": theme.get("central_works") or [],
        },
    )


def _topic_node(topic: dict[str, Any]) -> dict[str, Any]:
    return _node(
        topic["id"],
        "topic",
        str(topic.get("label") or topic["id"]),
        float(topic.get("size") or 0),
        theme_id=topic.get("theme_id"),
        topic_id=topic["id"],
        collection=topic.get("collection"),
        lang=topic.get("lang"),
        doc_kind=topic.get("doc_kind"),
        attrs={
            "service": topic.get("service"),
            "terms": topic.get("terms") or [],
            "authors": topic.get("authors") or [],
            "density": topic.get("density"),
            "author_purity": topic.get("author_purity"),
            "provenance": topic.get("provenance") or [],
            "central_works": topic.get("central_works") or [],
            "work_keys": topic.get("work_keys") or [],
        },
    )


def _work_node(payload: dict[str, Any], wk: str) -> dict[str, Any]:
    info = (payload.get("works") or {})[wk]
    assign = (payload.get("assignments") or {}).get(wk) or {}
    return _node(
        f"work:{wk}",
        "work",
        str(info.get("title") or wk),
        float(len(info.get("document_ids") or []) or 1),
        theme_id=assign.get("theme_id"),
        topic_id=assign.get("topic_id"),
        collection=info.get("collection"),
        lang=info.get("language"),
        doc_kind=info.get("doc_kind"),
        attrs={
            "work_key": wk,
            "author_key": info.get("author_key"),
            "author_ru": info.get("author_ru"),
            "document_ids": info.get("document_ids") or [],
            "filename": info.get("filename"),
        },
    )


def _hierarchy_edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    works = payload.get("works") or {}
    for theme in payload.get("themes") or []:
        for tid in theme.get("topic_ids") or []:
            edges.append(
                {
                    "id": make_edge_id(theme["id"], tid, "has_topic"),
                    "source": theme["id"],
                    "target": tid,
                    "type": "has_topic",
                    "weight": 1.0,
                    "weak": False,
                    "provenance": {"level": "hierarchy"},
                }
            )
    for topic in payload.get("topics") or []:
        for wk in topic.get("work_keys") or []:
            target = f"work:{wk}"
            edges.append(
                {
                    "id": make_edge_id(topic["id"], target, "has_work"),
                    "source": topic["id"],
                    "target": target,
                    "type": "has_work",
                    "weight": 1.0,
                    "weak": False,
                    "provenance": {"level": "hierarchy"},
                }
            )
    for wk, info in works.items():
        src_author = info.get("author_key")
        if src_author:
            author = f"author:{src_author}"
            target = f"work:{wk}"
            edges.append(
                {
                    "id": make_edge_id(author, target, "wrote"),
                    "source": author,
                    "target": target,
                    "type": "wrote",
                    "weight": 1.0,
                    "weak": False,
                    "provenance": {"author_key": src_author, "level": "structural"},
                }
            )
        for did in info.get("document_ids") or []:
            edges.append(
                {
                    "id": make_edge_id(did, f"work:{wk}", "part_of"),
                    "source": did,
                    "target": f"work:{wk}",
                    "type": "part_of",
                    "weight": 1.0,
                    "weak": False,
                    "provenance": {"level": "structural"},
                }
            )
    return edges


def _author_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    ru: dict[str, str] = {}
    for info in (payload.get("works") or {}).values():
        ak = info.get("author_key")
        if not ak:
            continue
        counts[str(ak)] += 1
        if info.get("author_ru"):
            ru[str(ak)] = str(info["author_ru"])
    return [
        _node(
            f"author:{ak}",
            "author",
            ru.get(ak) or ak,
            float(n),
            attrs={"author_key": ak, "author_ru": ru.get(ak), "works": n},
        )
        for ak, n in sorted(counts.items())
    ]


def _document_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = []
    assign = payload.get("assignments") or {}
    for wk, info in (payload.get("works") or {}).items():
        rel = assign.get(wk) or {}
        for did in info.get("document_ids") or []:
            nodes.append(
                _node(
                    did,
                    "document",
                    str(info.get("title") or did),
                    1,
                    theme_id=rel.get("theme_id"),
                    topic_id=rel.get("topic_id"),
                    collection=info.get("collection"),
                    lang=info.get("language"),
                    doc_kind=info.get("doc_kind"),
                    attrs={"work_key": wk, "filename": info.get("filename"), "document_id": did},
                )
            )
    return nodes


def _all_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [_theme_node(t) for t in payload.get("themes") or []]
    nodes += [_topic_node(t) for t in payload.get("topics") or []]
    nodes += [_work_node(payload, wk) for wk in sorted(payload.get("works") or {})]
    nodes += _author_nodes(payload)
    nodes += _document_nodes(payload)
    return nodes


def _all_edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get("work_edges") or []) + _hierarchy_edges(payload) + theme_link_weights(payload) + _topic_links(payload)


def view_overview(
    payload: dict[str, Any],
    *,
    limit: int | None = None,
    collection: str | None = None,
) -> dict[str, Any]:
    params = payload.get("params") or load_params()
    cap = node_limit(params, limit)
    themes = list(payload.get("themes") or [])
    if collection:
        wanted = collection
        keep_ids = set()
        for theme in themes:
            if theme.get("collection") == wanted:
                keep_ids.add(theme["id"])
                continue
            # any member work in the collection
            for tid in theme.get("topic_ids") or []:
                topic = _topic_by_id(payload).get(tid) or {}
                if topic.get("collection") == wanted:
                    keep_ids.add(theme["id"])
        themes = [t for t in themes if t["id"] in keep_ids]
    nodes = [_theme_node(t) for t in themes]
    ids = {n["id"] for n in nodes}
    edges = [e for e in theme_link_weights(payload) if e["source"] in ids and e["target"] in ids]
    return _envelope(
        payload,
        level="overview",
        nodes=nodes,
        edges=edges,
        limit=cap,
        extra={
            "collections": (payload.get("stats") or {}).get("collections") or [],
            "stats": payload.get("stats") or {},
            "stale": bool(payload.get("stale")),
        },
    )


def view_theme(payload: dict[str, Any], theme_id: str, *, limit: int | None = None) -> dict[str, Any] | None:
    theme = _theme_by_id(payload).get(theme_id)
    if not theme:
        return None
    params = payload.get("params") or load_params()
    cap = node_limit(params, limit)
    topics = _topic_by_id(payload)
    inside = [topics[tid] for tid in theme.get("topic_ids") or [] if tid in topics]
    inside_ids = {t["id"] for t in inside}
    links = _topic_links(payload)
    external_ids = set()
    kept_links = []
    for edge in links:
        a_in = edge["source"] in inside_ids
        b_in = edge["target"] in inside_ids
        if a_in and b_in:
            kept_links.append(edge)
        elif a_in or b_in:
            kept_links.append(edge)
            external_ids.add(edge["target"] if a_in else edge["source"])
    nodes = [_topic_node(t) for t in inside]
    for tid in sorted(external_ids):
        if tid in topics:
            node = _topic_node(topics[tid])
            node["attrs"]["external"] = True
            nodes.append(node)
    return _envelope(payload, level="theme", nodes=nodes, edges=kept_links, limit=cap, seed=theme_id)


def view_topic(payload: dict[str, Any], topic_id: str, *, limit: int | None = None) -> dict[str, Any] | None:
    topic = _topic_by_id(payload).get(topic_id)
    if not topic:
        return None
    params = payload.get("params") or load_params()
    cap = node_limit(params, limit)
    works = payload.get("works") or {}
    nodes = [_work_node(payload, wk) for wk in topic.get("work_keys") or [] if wk in works]
    author_ids = set()
    edges: list[dict[str, Any]] = []
    member_nodes = {n["id"] for n in nodes}
    for edge in payload.get("work_edges") or []:
        if edge["source"] in member_nodes and edge["target"] in member_nodes:
            edges.append(edge)
    for node in list(nodes):
        ak = (node.get("attrs") or {}).get("author_key")
        if ak:
            author_ids.add(str(ak))
    for ak in sorted(author_ids):
        count = sum(1 for n in nodes if (n.get("attrs") or {}).get("author_key") == ak)
        nodes.append(
            _node(
                f"author:{ak}",
                "author",
                ak,
                float(count),
                topic_id=topic_id,
                theme_id=topic.get("theme_id"),
                attrs={"author_key": ak},
            )
        )
        for n in nodes:
            if n["type"] == "work" and (n.get("attrs") or {}).get("author_key") == ak:
                edges.append(
                    {
                        "id": make_edge_id(f"author:{ak}", n["id"], "wrote"),
                        "source": f"author:{ak}",
                        "target": n["id"],
                        "type": "wrote",
                        "weight": 1.0,
                        "weak": False,
                        "provenance": {"author_key": ak},
                    }
                )
    neighbor_ids = set()
    for edge in _topic_links(payload):
        if edge["source"] == topic_id or edge["target"] == topic_id:
            edges.append(edge)
            neighbor_ids.add(edge["target"] if edge["source"] == topic_id else edge["source"])
    topics = _topic_by_id(payload)
    for tid in sorted(neighbor_ids):
        if tid in topics:
            node = _topic_node(topics[tid])
            node["attrs"]["neighbor"] = True
            nodes.append(node)
    return _envelope(payload, level="topic", nodes=nodes, edges=edges, limit=cap, seed=topic_id)


def _normalize_types(types: list[str] | None) -> set[str] | None:
    if not types:
        return None
    out = {t.strip() for t in types if t and t.strip()}
    if "in_theme" in out:
        out.add("in_collection")
    if "in_collection" in out:
        out.add("in_theme")
    return out


def view_local(
    payload: dict[str, Any],
    node_id: str,
    *,
    depth: int = 1,
    types: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any] | None:
    params = payload.get("params") or load_params()
    cap = node_limit(params, limit)
    depth = max(1, min(3, int(depth)))
    nodes = _all_nodes(payload)
    by_id = {n["id"]: n for n in nodes}
    if node_id not in by_id:
        return None
    edges = _all_edges(payload)
    allowed = _normalize_types(types)
    if allowed is not None:
        edges = [e for e in edges if e["type"] in allowed]
    adj: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adj[edge["source"]].append(edge["target"])
        adj[edge["target"]].append(edge["source"])
    seen = {node_id}
    frontier = [node_id]
    for _ in range(depth):
        nxt = []
        for nid in frontier:
            for nb in adj.get(nid, []):
                if nb not in seen:
                    seen.add(nb)
                    nxt.append(nb)
        frontier = nxt
    kept_nodes = [by_id[nid] for nid in sorted(seen) if nid in by_id]
    # seed first stability: sorted is fine; envelope ranks by size
    kept_edges = [e for e in edges if e["source"] in seen and e["target"] in seen]
    return _envelope(
        payload, level="local", nodes=kept_nodes, edges=kept_edges, limit=cap, seed=node_id
    )


def view_search(payload: dict[str, Any], q: str, *, limit: int | None = None) -> dict[str, Any]:
    params = payload.get("params") or load_params()
    cap = node_limit(params, limit)
    needle = (q or "").casefold().strip()
    nodes = _all_nodes(payload) if needle else []
    hits = []
    for node in nodes:
        blob = " ".join(
            [
                str(node.get("label") or ""),
                str(node.get("collection") or ""),
                str((node.get("attrs") or {}).get("author_key") or ""),
                str((node.get("attrs") or {}).get("author_ru") or ""),
                str((node.get("attrs") or {}).get("filename") or ""),
                " ".join(str(t.get("term") if isinstance(t, dict) else t) for t in (node.get("attrs") or {}).get("terms") or []),
            ]
        ).casefold()
        if needle in blob:
            hits.append(node)
    ids = {n["id"] for n in hits}
    edges = [e for e in _all_edges(payload) if e["source"] in ids and e["target"] in ids]
    return _envelope(payload, level="search", nodes=hits, edges=edges, limit=cap)


def view_node(payload: dict[str, Any], node_id: str, *, limit: int | None = None) -> dict[str, Any] | None:
    local = view_local(payload, node_id, depth=1, limit=limit)
    if not local:
        return None
    # Enrich the seed with documents and chunk refs.
    works = payload.get("works") or {}
    chunks: list[dict[str, str]] = []
    documents: list[dict[str, Any]] = []
    wk = node_id[5:] if node_id.startswith("work:") else None
    if wk is None:
        for key, info in works.items():
            if node_id in (info.get("document_ids") or []):
                wk = key
                break
    if wk and wk in works:
        info = works[wk]
        assign = (payload.get("assignments") or {}).get(wk) or {}
        for did in info.get("document_ids") or []:
            documents.append(
                {
                    "document_id": did,
                    "title": info.get("title"),
                    "filename": info.get("filename"),
                    "theme_id": assign.get("theme_id"),
                    "topic_id": assign.get("topic_id"),
                    "collection": info.get("collection"),
                }
            )
        for edge in payload.get("work_edges") or []:
            if wk in (str(edge["source"])[5:], str(edge["target"])[5:]):
                for cid in (edge.get("provenance") or {}).get("chunk_ids") or []:
                    chunks.append({"chunk_id": cid, "via": edge["type"], "edge_id": edge["id"]})
    for node in local["nodes"]:
        if node["id"] == node_id:
            node["attrs"]["documents"] = documents
            node["attrs"]["chunks"] = chunks[:30]
    local["meta"]["level"] = "node"
    return local


def view_edge(payload: dict[str, Any], edge_id: str) -> dict[str, Any] | None:
    for edge in _all_edges(payload):
        if edge["id"] != edge_id:
            continue
        nodes = []
        by_id = {n["id"]: n for n in _all_nodes(payload)}
        for nid in (edge["source"], edge["target"]):
            if nid in by_id:
                nodes.append(by_id[nid])
        return _envelope(
            payload,
            level="edge",
            nodes=nodes,
            edges=[edge],
            limit=10,
            extra={"provenance": edge.get("provenance") or {}},
        )
    return None


def read_published_index(tenant_id: UUID | str, db: Any = None) -> dict[str, Any] | None:
    if db is not None:
        try:
            from apps.api.services import graph_index as gi

            cached = gi.load_cached_index(db, tenant_id if isinstance(tenant_id, UUID) else UUID(str(tenant_id)))
            if cached:
                return cached
        except Exception:
            pass
    ptr = gcache.read_current_pointer(tenant_id) or {}
    name = ptr.get("index_file")
    if not name:
        return None
    path = gcache.graph_dir() / str(name)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
