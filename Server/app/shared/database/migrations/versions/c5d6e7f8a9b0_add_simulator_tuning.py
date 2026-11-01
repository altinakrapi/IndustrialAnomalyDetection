"""Add simulator tuning persistence.

Revision ID: c5d6e7f8a9b0
Revises: b3c4d5e6f7a8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "simulator_tuning_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="completed"),
        sa.Column("objective", sa.String(length=100), nullable=False),
        sa.Column("false_alarm_cap", sa.Float(), nullable=False),
        sa.Column("ground_truth_run_ids", sa.JSON(), nullable=False),
        sa.Column("data_seeds", sa.JSON(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("runtime_seconds", sa.Float(), nullable=False),
        sa.Column("limitations", sa.String(length=4000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_simulator_tuning_runs_id", "simulator_tuning_runs", ["id"])
    op.create_table(
        "simulator_tuned_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tuning_run_id", sa.Integer(), sa.ForeignKey("simulator_tuning_runs.id", ondelete="SET NULL")),
        sa.Column("model_config_id", sa.Integer(), sa.ForeignKey("model_configs.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("algorithm", sa.String(length=100), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("objective", sa.String(length=100), nullable=False),
        sa.Column("false_alarm_cap", sa.Float(), nullable=False),
        sa.Column("development_run_ids", sa.JSON(), nullable=False),
        sa.Column("development_seeds", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.String(length=4000), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", name="uq_simulator_tuned_configs_name"),
    )
    op.create_index("ix_simulator_tuned_configs_id", "simulator_tuned_configs", ["id"])


def downgrade() -> None:
    op.drop_index("ix_simulator_tuned_configs_id", table_name="simulator_tuned_configs")
    op.drop_table("simulator_tuned_configs")
    op.drop_index("ix_simulator_tuning_runs_id", table_name="simulator_tuning_runs")
    op.drop_table("simulator_tuning_runs")
