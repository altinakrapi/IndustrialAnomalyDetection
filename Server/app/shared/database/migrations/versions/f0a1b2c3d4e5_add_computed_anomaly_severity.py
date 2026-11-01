"""Add computed anomaly severity evidence.

Revision ID: f0a1b2c3d4e5
Revises: a4b5c6d7e8f9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
depends_on: Union[str, None] = None
branch_labels: Union[str, Sequence[str]] = None


def upgrade() -> None:
    op.add_column("anomalies", sa.Column("computed_severity", sa.String(length=30), nullable=True))
    op.add_column("anomalies", sa.Column("severity_reason", sa.String(length=500), nullable=True))
    op.add_column("anomalies", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("anomalies", "archived_at")
    op.drop_column("anomalies", "severity_reason")
    op.drop_column("anomalies", "computed_severity")
