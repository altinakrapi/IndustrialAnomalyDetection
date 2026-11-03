"""Prepare run 7 evidence restoration without creating automatic data.

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
depends_on: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tuning_run_exists = bind.execute(
        sa.text("SELECT id FROM simulator_tuning_runs WHERE id = 7")
    ).first()
    if tuning_run_exists is None:
        print(
            "e8f9a0b1c2d3: skipping run 7 tuned-evidence restoration "
            "because simulator_tuning_runs.id = 7 does not exist; "
            "run scripts/restore_tuning_evidence.py explicitly if needed"
        )
        return

    print(
        "e8f9a0b1c2d3: automatic evidence insertion is disabled; "
        "run scripts/restore_tuning_evidence.py explicitly if needed"
    )


def downgrade() -> None:
    return None
