"""Prevent duplicate anomaly rows for one analysis measurement.

Revision ID: 8d4c6a1b2e70
Revises: 2b8a6d41c9ef
Create Date: 2026-10-25 17:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d4c6a1b2e70"
down_revision: Union[str, None] = "2b8a6d41c9ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            "SELECT analysis_run_id, measurement_record_id "
            "FROM anomalies "
            "WHERE measurement_record_id IS NOT NULL "
            "GROUP BY analysis_run_id, measurement_record_id "
            "HAVING COUNT(*) > 1 "
            "LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot enforce anomaly uniqueness: duplicate records exist for "
            f"analysis {duplicate.analysis_run_id}, measurement "
            f"{duplicate.measurement_record_id}. Resolve these records before "
            "applying this migration; no anomaly data was modified."
        )
    op.create_index(
        "uq_anomalies_analysis_measurement",
        "anomalies",
        ["analysis_run_id", "measurement_record_id"],
        unique=True,
        postgresql_where=sa.text("measurement_record_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_anomalies_analysis_measurement", table_name="anomalies"
    )
