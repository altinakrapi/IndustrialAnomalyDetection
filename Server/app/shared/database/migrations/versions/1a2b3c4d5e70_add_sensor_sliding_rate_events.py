"""Add sliding sensor ingestion rate events.

Revision ID: 1a2b3c4d5e70
Revises: 9e2a7c4f1b6d
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "1a2b3c4d5e70"
down_revision: Union[str, None] = "9e2a7c4f1b6d"
branch_labels: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "device_api_keys",
        sa.Column(
            "rate_events",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("device_api_keys", "rate_events")
