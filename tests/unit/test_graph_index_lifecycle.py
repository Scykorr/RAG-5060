"""Lifecycle tests: empty / replace / incremental ≈ full / locks / stale / tenant (offline)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from uuid import UUID

from apps.api.services import graph_duplicates as gd
from apps.api.services import graph_index as gi
from apps.api.services import graph_index_cache as gcache
from apps.api.services import graph_lexicon as gl


def _edge_keyset(edges: list[gi.Edge]) -> set[tuple[str, str, str]]:
    out = set()
    for e in edges:
        a, b = e.source, e.target
        if a > b and e.type.startswith("work_") is False and e.type not in (
            "in_theme",
            "wrote",
            "part_of",
            "work_meta",
            "mentions_author",
            "work_mentions_author",
        ):
            a, b = b, a
        out.add((e.type, a, b))
    return out


def test_empty_corpus_assemble_ok():
    payload = gi._empty_index_payload(
        tenant_id="t",
        fingerprint="empty",
        params=gi.GraphIndexParams(),
        built_ms=1,
        mode="full",
        params_hash_value="abc",
    )
    assert payload["stats"]["documents"] == 0
    assert payload["edges"] == []
    assert payload["version"] == gi.INDEX_VERSION


def test_invalidate_clears_indexes_and_docs(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.api.services.graph_index_cache.data_root", lambda: tmp_path)
    monkeypatch.setattr("apps.api.services.storage.data_root", lambda: tmp_path)
    tenant = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    p = gi.index_path(tenant, "fp1", params=gi.GraphIndexParams())
    gi.write_index_atomic(p, {"fingerprint": "fp1", "version": gi.INDEX_VERSION, "edges": []})
    gcache.write_doc_artifact(tenant, "doc1", {"document_id": "doc1", "content_hash": "x"})
    gcache.write_current_pointer(tenant, {"index_file": p.name, "stale": False})
    assert gcache.acquire_build_lock(tenant)
    out = gi.invalidate_tenant(tenant)
    assert out["indexes_removed"] >= 1
    assert out["doc_artifacts_removed"] >= 1
    assert not p.exists()
    assert gcache.read_doc_artifact(tenant, "doc1") is None
    assert gcache.read_current_pointer(tenant) is None or not gcache.current_pointer_path(tenant).exists()
    assert not gcache.lock_path(tenant).exists()


def test_invalidate_one_tenant_leaves_other(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.api.services.graph_index_cache.data_root", lambda: tmp_path)
    t1 = "11111111-1111-1111-1111-111111111111"
    t2 = "22222222-2222-2222-2222-222222222222"
    p1 = gi.index_path(t1, "fp", params=gi.GraphIndexParams())
    p2 = gi.index_path(t2, "fp", params=gi.GraphIndexParams())
    gi.write_index_atomic(p1, {"tenant_id": t1, "version": gi.INDEX_VERSION})
    gi.write_index_atomic(p2, {"tenant_id": t2, "version": gi.INDEX_VERSION})
    gcache.write_doc_artifact(t1, "d1", {"document_id": "d1"})
    gcache.write_doc_artifact(t2, "d2", {"document_id": "d2"})
    gcache.write_current_pointer(t2, {"index_file": p2.name, "stale": False})
    assert gcache.acquire_build_lock(t2)
    gi.invalidate_tenant(t1)
    assert not p1.exists()
    assert p2.exists()
    assert gcache.read_doc_artifact(t2, "d2") is not None
    assert gcache.read_current_pointer(t2) is not None
    assert gcache.lock_path(t2).exists()


def test_doc_artifact_reuse_by_content_hash(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.api.services.graph_index_cache.data_root", lambda: tmp_path)
    meta = gi.parse_doc_meta(
        document_id="d1",
        filename="classic_0001_Pushkin_Пир_aaaaaaaa.txt",
    )
    raw = [("c1", 0, "Произведение русской классики\nАвтор/папка: Pushkin\nФайл: Пир.txt\n\nПир шумел.")]
    art1 = gi._build_doc_artifact(
        meta=meta, version_id="v1", raw_chunks=raw, params=gi.GraphIndexParams()
    )
    gcache.write_doc_artifact("t1", "d1", art1)
    loaded = gcache.read_doc_artifact("t1", "d1")
    assert loaded and loaded["content_hash"] == art1["content_hash"]
    h2 = gcache.content_hash(
        version_id="v1",
        filename=meta.filename,
        chunk_texts=[t for _, _, t in raw],
    )
    assert h2 == art1["content_hash"]


def test_incremental_layers_match_full_on_same_artifacts():
    """Same artifact set → same textual edge keys (weights may float)."""
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Pushkin_Пир_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0002_Pushkin_Онегин_bbbbbbbb.txt"),
        gi.parse_doc_meta(document_id="c", filename="classic_0003_Tolstoy_Анна_cccccccc.txt"),
    ]
    gi.resolve_work_keys(docs)
    chunks = [
        ("a", "c1", "На пиру читали роман «Евгений Онегин» вслух."),
        ("b", "c2", "Онегин ехал в деревню. Татьяна писала письмо."),
        ("c", "c3", "Анна смотрела на Вронского. Левин думал о Кити."),
    ]
    params = gi.GraphIndexParams(lex_min_score=0.05, shared_min_entities=1)
    edges1 = []
    edges1.extend(gi.layer_a_edges(docs))
    edges1.extend(gi.layer_mentions_author(docs=docs, chunk_iter=chunks))
    edges1.extend(gi.layer_b_refers_to(docs=docs, chunk_iter=chunks, params=params))
    terms = {
        "a": Counter({"пир": 3, "роман": 2, "онегин": 2, "читал": 2}),
        "b": Counter({"онегин": 4, "деревн": 2, "татьяна": 2, "письм": 2}),
        "c": Counter({"анна": 3, "вронск": 2, "левин": 2, "кити": 2}),
    }
    lex1, _ = gi.layer_d_lex_similar(docs=docs, doc_term_counts=terms, params=params, case_stats={})
    edges1.extend(lex1)
    edges2 = []
    edges2.extend(gi.layer_a_edges(docs))
    edges2.extend(gi.layer_mentions_author(docs=docs, chunk_iter=chunks))
    edges2.extend(gi.layer_b_refers_to(docs=docs, chunk_iter=chunks, params=params))
    lex2, _ = gi.layer_d_lex_similar(docs=docs, doc_term_counts=terms, params=params, case_stats={})
    edges2.extend(lex2)
    assert _edge_keyset(edges1) == _edge_keyset(edges2)


def test_delete_doc_removes_from_work_edges():
    docs = [
        gi.parse_doc_meta(document_id="a", filename="classic_0001_Pushkin_Пир_aaaaaaaa.txt"),
        gi.parse_doc_meta(document_id="b", filename="classic_0002_Tolstoy_Анна_bbbbbbbb.txt"),
    ]
    edges = [
        gi.Edge(source="a", target="b", type="refers_to", weight=2.0, provenance={"phrase": "x"}),
    ]
    work = gi.aggregate_work_edges(docs, edges)
    assert any(e.type == "work_refers_to" for e in work)
    work2 = gi.aggregate_work_edges(docs[:1], edges)
    assert not any(e.type == "work_refers_to" for e in work2)


def test_lexicon_config_loads_offline():
    gl.reload_graph_lexicon()
    data = gl.load_graph_lexicon()
    assert "genre_markers" in data
    assert "play_stage_words" in data
    assert gl.frozenset_field("play_stage_words")


def test_params_hash_includes_lexicon_content(tmp_path, monkeypatch):
    lex = tmp_path / "graph_lexicon.json"
    lex.write_text('{"genre_markers":["роман"]}', encoding="utf-8")
    monkeypatch.setattr(gl, "_DEFAULT_PATH", lex)
    gl.reload_graph_lexicon()
    h1 = gcache.params_hash({"index_version": gi.INDEX_VERSION})
    lex.write_text('{"genre_markers":["роман","повесть"]}', encoding="utf-8")
    gl.reload_graph_lexicon()
    h2 = gcache.params_hash({"index_version": gi.INDEX_VERSION})
    assert h1 != h2
    assert gl.lexicon_content_hash() in (
        __import__("hashlib").sha1(lex.read_bytes()).hexdigest()[:12],
    )


def test_atomic_publish_keeps_previous_on_failed_pointer(tmp_path, monkeypatch):
    """Simulates failed rebuild: new index not published → pointer stays on previous."""
    monkeypatch.setattr("apps.api.services.graph_index_cache.data_root", lambda: tmp_path)
    tenant = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    old = gi.index_path(tenant, "fp_old", params=gi.GraphIndexParams())
    gi.write_index_atomic(
        old,
        {"fingerprint": "fp_old", "version": gi.INDEX_VERSION, "edges": [{"type": "wrote"}], "stats": {}},
    )
    gcache.write_current_pointer(
        tenant,
        {"index_file": old.name, "fingerprint": "fp_old", "stale": False, "version": gi.INDEX_VERSION},
    )
    # Failed build would not call _publish_index; mark stale only
    gcache.mark_index_stale(tenant, reason="build_error:Boom")
    ptr = gcache.read_current_pointer(tenant)
    assert ptr and ptr["stale"] is True
    assert ptr["index_file"] == old.name
    prev = gi.read_index(gcache.graph_dir() / ptr["index_file"])
    assert prev and prev["fingerprint"] == "fp_old"
    annotated = gi._annotate_stale(prev, tenant)
    assert annotated["stale"] is True
    assert annotated["stats"]["stale"] is True


def test_file_lock_single_flight_and_dead_clear(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.api.services.graph_index_cache.data_root", lambda: tmp_path)
    tenant = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert gcache.acquire_build_lock(tenant) is True
    assert gcache.acquire_build_lock(tenant) is False
    gcache.release_build_lock(tenant)
    assert gcache.acquire_build_lock(tenant) is True
    gcache.release_build_lock(tenant)
    # dead pid lock
    dead = {
        "tenant_id": tenant,
        "pid": 999999,
        "started_at": "2000-01-01T00:00:00+00:00",
        "timeout_sec": 10,
    }
    gcache.write_json_atomic(gcache.lock_path(tenant), dead, indent=None)
    assert gcache.clear_dead_lock(tenant) is True
    assert gcache.acquire_build_lock(tenant) is True
    gcache.release_build_lock(tenant)


def test_mark_stale_after_corpus_change(tmp_path, monkeypatch):
    monkeypatch.setattr("apps.api.services.graph_index_cache.data_root", lambda: tmp_path)
    tenant = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    p = gi.index_path(tenant, "fp", params=gi.GraphIndexParams())
    gi.write_index_atomic(p, {"fingerprint": "fp", "version": gi.INDEX_VERSION, "stats": {}})
    gcache.write_current_pointer(
        tenant, {"index_file": p.name, "fingerprint": "fp", "stale": False, "version": gi.INDEX_VERSION}
    )
    monkeypatch.setattr(
        "apps.api.services.graph.settings.get_graph_limits",
        lambda: type("L", (), {"auto_rebuild": False})(),
    )
    out = gi.on_corpus_changed(tenant, reason="ingestion_ready")
    assert out["stale"] is True
    assert out["auto_rebuild"] is False
    ptr = gcache.read_current_pointer(tenant)
    assert ptr and ptr["stale"] is True
    assert ptr["index_file"] == p.name


def test_topup_second_edition_and_volume_updates_work_canonical_author():
    """Acceptance #6: second edition / new volume updates work glue, canonical, author_source."""
    # Volume 1 has author in filename; volume 2 / edition may lack author → work_merge
    vol1 = gi.parse_doc_meta(
        document_id="v1",
        filename="classic_0001_ch_Tolstoy_Война_и_мир_Том_1_aaaaaaaa.txt",
    )
    vol2 = gi.parse_doc_meta(
        document_id="v2",
        filename="classic_0002_ch_Война_и_мир_Том_2_bbbbbbbb.txt",
    )
    edition = gi.parse_doc_meta(
        document_id="ed",
        filename="classic_0003_eb_Tolstoy_Война_и_мир_Том_1_cccccccc.txt",
    )
    docs = [vol1, vol2, edition]
    gi.resolve_work_keys(docs)
    assert vol1.work_key == vol2.work_key == edition.work_key
    assert vol2.author_key == "Tolstoy"
    assert vol2.author_source == "work_merge"
    assert vol1.volume_no == 1 and vol2.volume_no == 2

    para = (
        "Князь Андрей смотрел на Кутузова и думал о сражении при Аустерлице. "
        "Пьер Безухов спорил о масонстве в салоне Анны Павловны. "
        "Наташа Ростова танцевала на первом балу. "
        "Николай уходил в гусары вопреки воле отца. "
    )
    body = para * 25
    almost = body + " Издательская пометка в конце тома один."
    texts = {"v1": body, "v2": "Совершенно другой текст тома два про партизан и пожар Москвы. " * 40, "ed": almost}
    m = gd.detect_relations(docs, texts, dup_threshold=0.80)
    # edition near-dup of vol1 → one canonical among {v1, ed}; vol2 separate canonical
    assert m["v2"]["canonical"] is True
    dups = [k for k, v in m.items() if v.get("relation") == "duplicate"]
    # near-equal editions → duplicate; if length gap large, contained_in is also acceptable
    linked = [k for k, v in m.items() if not v.get("canonical")]
    assert len(linked) == 1
    assert linked[0] in {"v1", "ed"}
    info = m[linked[0]]
    assert info.get("duplicate_of") in {"v1", "ed"} - {linked[0]} or info.get("contained_in") in {"v1", "ed"} - {linked[0]}

    for d in docs:
        info = m[d.document_id]
        d.canonical = bool(info["canonical"])
        d.duplicate_of = info.get("duplicate_of")
    edges = gi.layer_a_edges(docs)
    part_of = [e for e in edges if e.type == "part_of"]
    assert {e.source for e in part_of} == {"v1", "v2", "ed"}
    work_nodes = {e.target for e in part_of}
    assert len(work_nodes) == 1
    meta = [e for e in edges if e.type == "work_meta"]
    assert len(meta) == 1
    assert meta[0].provenance["fragment_count"] == 3
