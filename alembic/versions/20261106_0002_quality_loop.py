"""quality eval sets and runs

Revision ID: 20261106_0002
Revises: 20261103_0001
Create Date: 2026-11-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261106_0002"
down_revision = "20261103_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "eval_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("eval_set_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("eval_sets.id"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("gold_answers", postgresql.JSONB(), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("split", sa.String(32), nullable=False, server_default="calibration"),
        sa.Column("method", sa.String(32), nullable=False, server_default="import"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "quality_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("eval_set_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("eval_sets.id"), nullable=True),
        sa.Column("status", sa.String(32), server_default="queued"),
        sa.Column("attempt", sa.Integer(), server_default="0"),
        sa.Column("max_attempts", sa.Integer(), server_default="8"),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=False, server_default="0.55"),
        sa.Column("level", sa.String(32), nullable=True),
        sa.Column("corpus_fingerprint", sa.String(128), nullable=True),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("artifact_dir", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("quality_runs")
    op.drop_table("eval_items")
    op.drop_table("eval_sets")
