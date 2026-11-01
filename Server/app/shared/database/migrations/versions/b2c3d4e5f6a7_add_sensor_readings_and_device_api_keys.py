"""Add sensor reading metadata and per-device API credentials.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-27 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "source_type",
            sa.String(length=30),
            server_default="manual",
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE devices SET source_type = 'dataset' WHERE is_registered IS FALSE"
    )

    op.add_column(
        "equipment_measurements",
        sa.Column(
            "source_type",
            sa.String(length=30),
            server_default="manual",
            nullable=False,
        ),
    )
    op.add_column(
        "equipment_measurements",
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.add_column(
        "equipment_measurements",
        sa.Column("sensor_timestamp", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE equipment_measurements
        SET source_type = CASE
            WHEN source = 'synthetic_test' THEN 'simulated'
            WHEN source = 'sensor' THEN 'sensor'
            WHEN source = 'dataset' THEN 'dataset'
            ELSE 'manual'
        END,
        received_at = recorded_at
        """
    )
    op.execute(
        """
        UPDATE devices
        SET source_type = 'simulated'
        WHERE EXISTS (
            SELECT 1
            FROM equipment_parameters
            JOIN equipment_measurements
              ON equipment_measurements.parameter_id = equipment_parameters.id
            WHERE equipment_parameters.device_id = devices.id
              AND equipment_measurements.source_type = 'simulated'
        )
        """
    )
    op.alter_column(
        "equipment_measurements",
        "received_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.create_index(
        "ix_equipment_measurements_source_received_at",
        "equipment_measurements",
        ["source_type", "received_at"],
    )

    op.create_table(
        "device_api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=12), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "rate_window_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "rate_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_index(
        "ix_device_api_keys_device_id",
        "device_api_keys",
        ["device_id"],
    )
    op.create_index(
        "uq_device_api_keys_one_active_per_device",
        "device_api_keys",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.add_column(
        "equipment_measurements",
        sa.Column("sensor_api_key_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "equipment_measurements_sensor_api_key_id_fkey",
        "equipment_measurements",
        "device_api_keys",
        ["sensor_api_key_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "equipment_measurements_sensor_api_key_id_fkey",
        "equipment_measurements",
        type_="foreignkey",
    )
    op.drop_column("equipment_measurements", "sensor_api_key_id")
    op.drop_index(
        "uq_device_api_keys_one_active_per_device",
        table_name="device_api_keys",
    )
    op.drop_index("ix_device_api_keys_device_id", table_name="device_api_keys")
    op.drop_column("device_api_keys", "rate_count")
    op.drop_column("device_api_keys", "rate_window_started_at")
    op.drop_table("device_api_keys")
    op.drop_index(
        "ix_equipment_measurements_source_received_at",
        table_name="equipment_measurements",
    )
    op.drop_column("equipment_measurements", "sensor_timestamp")
    op.drop_column("equipment_measurements", "received_at")
    op.drop_column("equipment_measurements", "source_type")
    op.drop_column("devices", "source_type")
