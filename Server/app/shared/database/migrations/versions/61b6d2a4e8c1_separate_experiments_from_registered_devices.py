"""Allow historical datasets and orders without registered equipment.

Revision ID: 61b6d2a4e8c1
Revises: 4f9a2c1e6b73
Create Date: 2026-10-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "61b6d2a4e8c1"
down_revision: Union[str, None] = "4f9a2c1e6b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "is_registered",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE devices
            SET is_registered = FALSE
            WHERE device_code ~ '^NASA-IMS-TESTSET-[0-9]+$'
              AND device_type = 'Bearing test set'
              AND category = 'NASA IMS historical experiment'
            """
        )
    )

    op.drop_constraint("datasets_device_id_fkey", "datasets", type_="foreignkey")
    op.alter_column(
        "datasets",
        "device_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_foreign_key(
        "datasets_device_id_fkey",
        "datasets",
        "devices",
        ["device_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        sa.text("UPDATE datasets SET device_id = NULL WHERE file_format = 'nasa_ims'")
    )

    op.drop_constraint(
        "maintenance_orders_device_id_fkey",
        "maintenance_orders",
        type_="foreignkey",
    )
    op.alter_column(
        "maintenance_orders",
        "device_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_foreign_key(
        "maintenance_orders_device_id_fkey",
        "maintenance_orders",
        "devices",
        ["device_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        sa.text(
            """
            UPDATE maintenance_orders AS orders
            SET device_id = NULL
            FROM devices
            WHERE orders.device_id = devices.id
              AND devices.is_registered = FALSE
            """
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_unlinked_datasets = connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM datasets WHERE device_id IS NULL)")
    )
    has_unlinked_orders = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM maintenance_orders WHERE device_id IS NULL)"
        )
    )
    if has_unlinked_datasets or has_unlinked_orders:
        raise RuntimeError(
            "Cannot restore required device links while datasets or work orders "
            "are unassociated; doing so would invent relationships."
        )

    op.drop_constraint("datasets_device_id_fkey", "datasets", type_="foreignkey")
    op.alter_column(
        "datasets",
        "device_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        "datasets_device_id_fkey",
        "datasets",
        "devices",
        ["device_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "maintenance_orders_device_id_fkey",
        "maintenance_orders",
        type_="foreignkey",
    )
    op.alter_column(
        "maintenance_orders",
        "device_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        "maintenance_orders_device_id_fkey",
        "maintenance_orders",
        "devices",
        ["device_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_column("devices", "is_registered")
