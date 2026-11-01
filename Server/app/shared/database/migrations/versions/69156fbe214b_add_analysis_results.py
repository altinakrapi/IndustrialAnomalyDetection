"""persist normal and anomalous predictions for each analysis run

Revision ID: 69156fbe214b
Revises: ba582bbb8fe5
Create Date: 2026-10-19 19:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "69156fbe214b"
down_revision: Union[str, None] = "ba582bbb8fe5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), nullable=False),
        sa.Column("measurement_record_id", sa.Integer(), nullable=True),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("is_anomaly", sa.Boolean(), nullable=False),
        sa.Column("anomaly_score", sa.Float(), nullable=False),
        sa.Column("feature_values", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["measurement_record_id"],
            ["measurement_records.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_results_run_row",
        "analysis_results",
        ["analysis_run_id", "source_row_number"],
        unique=True,
    )
    op.create_index(
        op.f("ix_analysis_results_id"), "analysis_results", ["id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_analysis_results_id"), table_name="analysis_results")
    op.drop_index("ix_analysis_results_run_row", table_name="analysis_results")
    op.drop_table("analysis_results")
