"""persist NASA IMS dataset import progress

Revision ID: 7c4a2e9d1f6b
Revises: 52c110d75909
Create Date: 2026-10-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c4a2e9d1f6b"
down_revision: Union[str, None] = "93c1479a4b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dataset_import_jobs",
        sa.Column("source_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("downloaded_bytes", sa.BigInteger(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=True),
        sa.Column("dataset_ids", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("source_key"),
    )


def downgrade() -> None:
    op.drop_table("dataset_import_jobs")
