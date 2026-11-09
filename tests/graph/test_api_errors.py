"""Graph router error shaping — import smoke."""

from __future__ import annotations

from apps.api.routers import graph as graph_router
from apps.api.main import app


def test_graph_routes_registered():
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/graph/status" in paths
    assert "/api/graph/neighborhood/{document_id}" in paths
    assert "/api/graph/edges/{edge_id}/evidence" in paths
    assert graph_router.router.prefix == "/api/graph"
