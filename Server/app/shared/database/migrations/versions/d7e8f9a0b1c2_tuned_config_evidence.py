"""Store simulator tuned configuration evidence.

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "c6d7e8f9a0b1"
depends_on: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "simulator_tuned_configs",
        sa.Column("evidence", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("simulator_tuned_configs", "evidence")
