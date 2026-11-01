"""Assign a responsible user to registered equipment.

Revision ID: a1b2c3d4e5f6
Revises: e5f6a7b8c9d0
Create Date: 2026-10-26 21:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "equipment_parameters",
        "expected_interval_minutes",
        existing_type=sa.Integer(),
        server_default="120",
    )
    op.alter_column(
        "equipment_parameters",
        "analysis_method",
        existing_type=sa.String(length=30),
        server_default="isolation_forest",
    )
    op.add_column(
        "devices",
        sa.Column("responsible_user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "devices_responsible_user_id_fkey",
        "devices",
        "users",
        ["responsible_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_devices_responsible_user_id",
        "devices",
        ["responsible_user_id"],
        unique=False,
    )
    op.execute(
        """
        UPDATE devices
        SET responsible_user_id = created_by
        WHERE is_registered IS TRUE
          AND responsible_user_id IS NULL
          AND created_by IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_devices_responsible_user_id", table_name="devices")
    op.drop_constraint(
        "devices_responsible_user_id_fkey", "devices", type_="foreignkey"
    )
    op.drop_column("devices", "responsible_user_id")
    op.alter_column(
        "equipment_parameters",
        "analysis_method",
        existing_type=sa.String(length=30),
        server_default=None,
    )
    op.alter_column(
        "equipment_parameters",
        "expected_interval_minutes",
        existing_type=sa.Integer(),
        server_default=None,
    )
