"""Allow threshold-only analysis results to omit an algorithm score.

Revision ID: d4a5b6c7d8e9
Revises: c3f54a71be20
Create Date: 2026-10-26 20:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a5b6c7d8e9"
down_revision: Union[str, None] = "c3f54a71be20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "analysis_results",
        "anomaly_score",
        existing_type=sa.Float(),
        nullable=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_unscored_results = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM analysis_results WHERE anomaly_score IS NULL)"
        )
    )
    if has_unscored_results:
        raise RuntimeError(
            "Cannot require algorithm scores while unscored analysis results exist."
        )
    op.alter_column(
        "analysis_results",
        "anomaly_score",
        existing_type=sa.Float(),
        nullable=False,
    )
