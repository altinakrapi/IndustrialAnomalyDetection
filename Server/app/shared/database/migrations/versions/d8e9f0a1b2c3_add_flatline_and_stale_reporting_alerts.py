"""add flatline settings and second-based reporting intervals

Revision ID: d8e9f0a1b2c3
Revises: c1d2e3f4a5b6
Create Date: 2026-10-28 21:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "equipment_parameters",
        sa.Column("expected_interval_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "equipment_parameters",
        sa.Column(
            "flatline_reading_count",
            sa.Integer(),
            server_default="10",
            nullable=False,
        ),
    )
    op.add_column(
        "equipment_parameters",
        sa.Column(
            "flatline_variance_tolerance",
            sa.Float(),
            server_default="0.05",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_equipment_parameter_interval_seconds_positive",
        "equipment_parameters",
        "expected_interval_seconds IS NULL OR expected_interval_seconds > 0",
    )
    op.create_check_constraint(
        "ck_equipment_parameter_flatline_reading_count_range",
        "equipment_parameters",
        "flatline_reading_count BETWEEN 2 AND 100",
    )
    op.create_check_constraint(
        "ck_equipment_parameter_flatline_tolerance_range",
        "equipment_parameters",
        "flatline_variance_tolerance > 0 AND "
        "flatline_variance_tolerance <= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_equipment_parameter_flatline_tolerance_range",
        "equipment_parameters",
        type_="check",
    )
    op.drop_constraint(
        "ck_equipment_parameter_flatline_reading_count_range",
        "equipment_parameters",
        type_="check",
    )
    op.drop_constraint(
        "ck_equipment_parameter_interval_seconds_positive",
        "equipment_parameters",
        type_="check",
    )
    op.drop_column("equipment_parameters", "flatline_variance_tolerance")
    op.drop_column("equipment_parameters", "flatline_reading_count")
    op.drop_column("equipment_parameters", "expected_interval_seconds")
