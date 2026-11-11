"""Level API: overview, themes, topics, local, search, edges, nodes, rebuild."""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from apps.api.auth import AuthUser, get_current_user
from apps.api.db import get_db
from apps.api.main import app
from apps.api.services import graph_index_cache as gcache
from apps.api.services import graph_topics as gt
from tests.unit.test_graph_topics import _clique, _doc, _index, _params


TENANT = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _build_payload():
    ru = [f"ru{i}" for i in range(4)]
    en = [f"en{i}" for i in range(4)]
    docs = [_doc(k, f"Война {k}", "Tolstoy") for k in ru] + [
        _doc(k, f"TLS {k}", "IETF", collection="tech_", lang="en", kind="technical") for k in en
    ]
    edges = _clique(ru, 1.0, ["война", "полк"]) + _clique(en, 1.0, ["protocol", "cipher"])
    # a dangling document edge so local depth reaches a document via part_of
    return gt.build_topics_from_index(
        _index(docs, edges),
        params=_params(topic_resolution=1.0, theme_resolution=1.0),
        tenant_id=str(TENANT),
    )


def test_level_endpoints_etag_depth_types_and_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr(gcache, "data_root", lambda: tmp_path)
    monkeypatch.setattr("apps.api.main.init_db", lambda: None)
    payload = _build_payload()
    gt.save_topics(TENANT, payload)
    index = _index([], [])
    gcache.write_json_atomic(gcache.graph_dir() / "idx.json", _index(
        [_doc(k, k, "Tolstoy") for k in ["ru0", "ru1", "ru2", "ru3"]],
        [],
    ))
    # publish the same index the topics were built from so scope=topics can rebuild
    full_index = {
        "version": 16,
        "fingerprint": "fp-test",
        "params_hash": "ph",
        "documents": [
            _doc(k, f"Война {k}", "Tolstoy") for k in [f"ru{i}" for i in range(4)]
        ] + [
            _doc(k, f"TLS {k}", "IETF", collection="tech_", lang="en", kind="technical")
            for k in [f"en{i}" for i in range(4)]
        ],
        "edges": _clique([f"ru{i}" for i in range(4)], 1.0, ["война"])
        + _clique([f"en{i}" for i in range(4)], 1.0, ["protocol"]),
        "stats": {"build_mode": "full", "lifecycle": {"added": 0, "changed": 0, "removed": 0, "cached": 8}},
        "stale": False,
    }
    gcache.write_json_atomic(gcache.graph_dir() / "idx.json", full_index)
    gcache.write_current_pointer(
        TENANT,
        {
            "index_file": "idx.json",
            "fingerprint": "fp-test",
            "params_hash": "ph",
            "version": 16,
            "stale": False,
        },
    )

    user = AuthUser(
        id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        login="admin",
        tenant_id=TENANT,
        role="admin",
    )
    app.dependency_overrides[get_current_user] = lambda: user

    def _db():
        yield None

    app.dependency_overrides[get_db] = _db
    try:
        with TestClient(app) as client:
            overview = client.get("/api/graph/overview?view=levels")
            assert overview.status_code == 200
            body = overview.json()
            assert body["meta"]["level"] == "overview"
            assert body["meta"]["limit"] == 1500
            assert body["nodes"]
            assert body["nodes"][0]["type"] == "theme"
            assert "collections" in body
            tag = overview.headers["etag"]
            cached = client.get("/api/graph/overview?view=levels", headers={"If-None-Match": tag})
            assert cached.status_code == 304

            capped = client.get("/api/graph/overview?view=levels&limit=99999")
            assert capped.json()["meta"]["limit"] == 3000
            truncated = client.get("/api/graph/overview?view=levels&limit=1")
            assert truncated.json()["meta"]["truncated"] is True
            assert len(truncated.json()["nodes"]) == 1

            theme_id = body["nodes"][0]["id"]
            theme = client.get(f"/api/graph/themes/{theme_id}")
            assert theme.status_code == 200
            assert theme.json()["meta"]["level"] == "theme"
            assert any(n["type"] == "topic" for n in theme.json()["nodes"])

            topic_id = next(n["id"] for n in theme.json()["nodes"] if n["type"] == "topic")
            topic = client.get(f"/api/graph/topics/{topic_id}")
            assert topic.status_code == 200
            assert any(n["type"] == "work" for n in topic.json()["nodes"])
            assert any(n["type"] == "author" for n in topic.json()["nodes"])

            d1 = client.get(f"/api/graph/local/{theme_id}?depth=1")
            d2 = client.get(f"/api/graph/local/{theme_id}?depth=2")
            d3 = client.get(f"/api/graph/local/{theme_id}?depth=3")
            assert d1.status_code == d2.status_code == d3.status_code == 200
            n1, n2, n3 = (len(r.json()["nodes"]) for r in (d1, d2, d3))
            assert n1 < n2 < n3
            filtered = client.get(f"/api/graph/local/{theme_id}?depth=3&types=has_topic")
            kinds = {n["type"] for n in filtered.json()["nodes"]}
            assert "topic" in kinds
            assert "document" not in kinds
            assert "work" not in kinds

            work = next(n for n in topic.json()["nodes"] if n["type"] == "work")
            found = client.get("/api/graph/search", params={"q": work["label"][:6]})
            assert found.status_code == 200
            assert any(n["id"] == work["id"] for n in found.json()["nodes"])

            card = client.get(f"/api/graph/nodes/{work['id']}")
            assert card.status_code == 200
            assert card.json()["meta"]["level"] == "node"
            seed = next(n for n in card.json()["nodes"] if n["id"] == work["id"])
            assert "documents" in seed["attrs"]

            edge_id = topic.json()["edges"][0]["id"]
            edge = client.get(f"/api/graph/edges/{edge_id}")
            assert edge.status_code == 200
            assert edge.json()["edges"][0]["id"] == edge_id
            assert "provenance" in edge.json()

            rebuilt = client.post("/api/graph/rebuild?scope=topics&sync=true")
            assert rebuilt.status_code == 200
            assert rebuilt.json()["status"] == "ok"
            assert rebuilt.json()["scope"] == "topics"
            assert gt.load_topics(TENANT)
    finally:
        app.dependency_overrides.clear()
