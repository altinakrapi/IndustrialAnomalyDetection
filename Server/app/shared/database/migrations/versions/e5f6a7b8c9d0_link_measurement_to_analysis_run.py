"""Link equipment measurement executions directly to their source measurement.

Revision ID: e5f6a7b8c9d0
Revises: d4a5b6c7d8e9
Create Date: 2026-10-26 20:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("equipment_measurement_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "analysis_runs_equipment_measurement_id_fkey",
        "analysis_runs",
        "equipment_measurements",
        ["equipment_measurement_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_analysis_runs_equipment_measurement_id",
        "analysis_runs",
        ["equipment_measurement_id"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_linked_runs = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM analysis_runs "
            "WHERE equipment_measurement_id IS NOT NULL)"
        )
    )
    if has_linked_runs:
        raise RuntimeError(
            "Cannot remove equipment source links while analysis history uses them."
        )
    op.drop_index(
        "ix_analysis_runs_equipment_measurement_id", table_name="analysis_runs"
    )
    op.drop_constraint(
        "analysis_runs_equipment_measurement_id_fkey",
        "analysis_runs",
        type_="foreignkey",
    )
    op.drop_column("analysis_runs", "equipment_measurement_id")
