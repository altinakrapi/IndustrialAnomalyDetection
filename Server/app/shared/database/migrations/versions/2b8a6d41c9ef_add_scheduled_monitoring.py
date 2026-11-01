"""add scheduled monitoring and idempotent notification metadata

Revision ID: 2b8a6d41c9ef
Revises: 7c4a2e9d1f6b
Create Date: 2026-10-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2b8a6d41c9ef"
down_revision: Union[str, None] = "7c4a2e9d1f6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("execution_type", sa.String(length=30), nullable=False, server_default="manual"),
    )
    op.alter_column("analysis_runs", "execution_type", server_default=None)
    op.add_column(
        "analysis_runs", sa.Column("execution_key", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "analysis_runs", sa.Column("source_start_row", sa.Integer(), nullable=True)
    )
    op.add_column(
        "analysis_runs", sa.Column("source_end_row", sa.Integer(), nullable=True)
    )
    op.create_index(
        "uq_analysis_runs_execution_key",
        "analysis_runs",
        ["execution_key"],
        unique=True,
    )
    op.add_column(
        "notifications",
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("notifications", "details", server_default=None)
    op.add_column(
        "notifications",
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "uq_notifications_user_dedupe_key",
        "notifications",
        ["user_id", "dedupe_key"],
        unique=True,
    )
    op.create_table(
        "monitoring_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("simulation_enabled", sa.Boolean(), nullable=False),
        sa.Column("simulation_batch_size", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO monitoring_schedules "
            "(id, enabled, interval_minutes, simulation_enabled, "
            "simulation_batch_size, next_run_at) "
            "VALUES (1, TRUE, 60, FALSE, 100, NOW())"
        )
    )


def downgrade() -> None:
    op.drop_table("monitoring_schedules")
    op.drop_index(
        "uq_notifications_user_dedupe_key", table_name="notifications"
    )
    op.drop_column("notifications", "dedupe_key")
    op.drop_column("notifications", "details")
    op.drop_index("uq_analysis_runs_execution_key", table_name="analysis_runs")
    op.drop_column("analysis_runs", "source_end_row")
    op.drop_column("analysis_runs", "source_start_row")
    op.drop_column("analysis_runs", "execution_key")
    op.drop_column("analysis_runs", "execution_type")
