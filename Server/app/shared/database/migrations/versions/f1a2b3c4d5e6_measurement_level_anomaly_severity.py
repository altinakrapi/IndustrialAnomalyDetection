"""Add stored measurement-level anomaly severity.

Revision ID: f1a2b3c4d5e6
Revises: f0a1b2c3d4e5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
depends_on: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("equipment_measurements", sa.Column("measurement_severity", sa.String(length=30), nullable=True))
    op.add_column("equipment_measurements", sa.Column("measurement_severity_reason", sa.String(length=500), nullable=True))
    op.add_column("equipment_measurements", sa.Column("measurement_detectors", sa.JSON(), nullable=True))
    op.add_column("equipment_measurements", sa.Column("measurement_agreeing_detectors", sa.Integer(), nullable=True))
    op.add_column("equipment_measurements", sa.Column("measurement_rule_fired", sa.String(length=100), nullable=True))
    op.add_column("equipment_measurements", sa.Column("measurement_analysis_run_ids", sa.JSON(), nullable=True))
    op.add_column("equipment_measurements", sa.Column("measurement_severity_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("equipment_measurements", "measurement_severity_updated_at")
    op.drop_column("equipment_measurements", "measurement_analysis_run_ids")
    op.drop_column("equipment_measurements", "measurement_rule_fired")
    op.drop_column("equipment_measurements", "measurement_agreeing_detectors")
    op.drop_column("equipment_measurements", "measurement_detectors")
    op.drop_column("equipment_measurements", "measurement_severity_reason")
    op.drop_column("equipment_measurements", "measurement_severity")
