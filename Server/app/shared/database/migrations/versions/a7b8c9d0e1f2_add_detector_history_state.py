"""store detector history state on analysis results

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_results",
        sa.Column("history_state", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "analysis_results",
        sa.Column("history_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "analysis_results",
        sa.Column("history_required", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_results", "history_required")
    op.drop_column("analysis_results", "history_size")
    op.drop_column("analysis_results", "history_state")
