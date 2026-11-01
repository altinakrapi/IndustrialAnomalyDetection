"""Mark tuning-run 2 simulator configurations as superseded.

Revision ID: c6d7e8f9a0b1
Revises: c5d6e7f8a9b0
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, None] = "c5d6e7f8a9b0"
depends_on: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None

SUPERSEDED_REASON = "superseded: computed with an incorrect metric"


def upgrade() -> None:
    op.add_column(
        "simulator_tuned_configs",
        sa.Column("is_superseded", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "simulator_tuned_configs",
        sa.Column("superseded_reason", sa.String(length=500), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE simulator_tuned_configs "
            "SET is_superseded = TRUE, superseded_reason = :reason "
            "WHERE tuning_run_id = 2"
        ).bindparams(reason=SUPERSEDED_REASON)
    )
    op.alter_column(
        "simulator_tuned_configs",
        "is_superseded",
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_column("simulator_tuned_configs", "superseded_reason")
    op.drop_column("simulator_tuned_configs", "is_superseded")
