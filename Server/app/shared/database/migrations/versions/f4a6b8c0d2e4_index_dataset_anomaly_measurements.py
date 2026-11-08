"""Index dataset measurement IDs for distinct summary counts.

Revision ID: f4a6b8c0d2e4
Revises: b3c4d5e6f7a8
"""

from alembic import op


revision = "f4a6b8c0d2e4"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_anomalies_measurement_record_id_not_null "
        "ON anomalies (measurement_record_id) "
        "WHERE measurement_record_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_anomalies_measurement_record_id_not_null",
        table_name="anomalies",
    )
