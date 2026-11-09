"""document graph edges, evidence, jobs, review events

Revision ID: 20261109_0003
Revises: 20261106_0002
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261109_0003"
down_revision = "20261106_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "source_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id"),
            nullable=False,
        ),
        sa.Column(
            "target_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id"),
            nullable=False,
        ),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("relation_type", sa.String(64), nullable=False),
        sa.Column("directed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("review_state", sa.String(32), nullable=False, server_default="PROPOSED"),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("score_method", sa.String(64), nullable=True),
        sa.Column("algorithm_name", sa.String(64), nullable=False),
        sa.Column("algorithm_version", sa.String(32), nullable=False, server_default="1"),
        sa.Column("model_id", sa.String(200), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("detector_key", sa.String(128), nullable=False),
        sa.Column("edge_key", sa.String(128), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stale_reason", sa.String(200), nullable=True),
        sa.Column("payload_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "edge_key", name="uq_graph_edges_tenant_edge_key"),
    )
    op.create_index(
        "ix_graph_edges_src",
        "graph_edges",
        ["tenant_id", "source_document_id", "relation_type", "review_state"],
    )
    op.create_index(
        "ix_graph_edges_tgt",
        "graph_edges",
        ["tenant_id", "target_document_id", "relation_type", "review_state"],
    )

    op.create_table(
        "graph_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "edge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("graph_edges.id"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("source_document_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_document_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section_path", postgresql.JSONB(), nullable=True),
        sa.Column("span_start", sa.Integer(), nullable=True),
        sa.Column("span_end", sa.Integer(), nullable=True),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("score_raw", sa.Float(), nullable=True),
        sa.Column("score_normalized", sa.Float(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="VALID"),
        sa.Column("evidence_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("edge_id", "evidence_key", name="uq_graph_evidence_edge_key"),
    )
    op.create_index("ix_graph_evidence_edge", "graph_evidence", ["edge_id"])

    op.create_table(
        "graph_build_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("mode", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("stage", sa.String(64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("safe_error", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_version", sa.String(32), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_graph_jobs_state", "graph_build_jobs", ["tenant_id", "state", "created_at"])

    op.create_table(
        "graph_review_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "edge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("graph_edges.id"),
            nullable=False,
        ),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("old_state", sa.String(32), nullable=True),
        sa.Column("new_state", sa.String(32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("graph_review_events")
    op.drop_table("graph_build_jobs")
    op.drop_table("graph_evidence")
    op.drop_index("ix_graph_edges_tgt", table_name="graph_edges")
    op.drop_index("ix_graph_edges_src", table_name="graph_edges")
    op.drop_table("graph_edges")
