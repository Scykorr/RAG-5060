"""Graph module limits — env via existing Settings + local defaults."""

from __future__ import annotations

from dataclasses import dataclass

from apps.api.settings import get_settings


@dataclass(frozen=True)
class GraphLimits:
    enabled: bool = True
    llm_analysis_enabled: bool = False
    default_depth: int = 1
    max_depth: int = 3
    default_nodes: int = 200
    hard_max_nodes: int = 500
    default_edges: int = 400
    hard_max_edges: int = 1500
    neighbor_top_k: int = 12
    lexical_min_score: float = 0.22
    build_concurrency: int = 1
    export_enabled: bool = False
    evidence_snippet_chars: int = 280
    representative_chunks: int = 4
    algorithm_version: str = "1"


def get_graph_limits() -> GraphLimits:
    # Keep env surface small; hard limits stay in code for MVP safety.
    _ = get_settings()
    return GraphLimits()
