"""Backfill sensor provenance on devices with historic sensor readings.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-27 21:30:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE devices
        SET source_type = 'sensor'
        WHERE is_registered IS TRUE
          AND source_type = 'manual'
          AND EXISTS (
              SELECT 1
              FROM equipment_parameters
              JOIN equipment_measurements
                ON equipment_measurements.parameter_id = equipment_parameters.id
              WHERE equipment_parameters.device_id = devices.id
                AND equipment_measurements.source_type = 'sensor'
          )
        """
    )


def downgrade() -> None:
    pass
