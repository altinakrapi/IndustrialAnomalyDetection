"""Keep NASA IMS test sets attached to their own historical experiment devices.

Revision ID: 4f9a2c1e6b73
Revises: 8d4c6a1b2e70
Create Date: 2026-10-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4f9a2c1e6b73"
down_revision: Union[str, None] = "8d4c6a1b2e70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            WITH test_sets AS (
                SELECT DISTINCT
                    (regexp_match(name, ' - ([0-9]+)(st|nd|rd|th)_test$', 'i'))[1]::integer
                        AS test_set,
                    uploaded_by
                FROM datasets
                WHERE file_format = 'nasa_ims'
                  AND name ~* ' - [0-9]+(st|nd|rd|th)_test$'
            )
            INSERT INTO devices (
                name, device_code, device_type, category, model, description,
                status, created_by
            )
            SELECT
                'NASA IMS Bearing Test Set ' || test_set,
                'NASA-IMS-TESTSET-' || test_set,
                'Bearing test set',
                'NASA IMS historical experiment',
                'NASA IMS ' || test_set || ' test set',
                'Historical NASA IMS experimental bearing dataset. This record '
                    || 'is not a live physical equipment connection.',
                'active',
                uploaded_by
            FROM test_sets
            ON CONFLICT (device_code) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE datasets AS dataset
            SET device_id = device.id
            FROM devices AS device
            WHERE dataset.file_format = 'nasa_ims'
              AND dataset.name ~* ' - [0-9]+(st|nd|rd|th)_test$'
              AND device.device_code = 'NASA-IMS-TESTSET-'
                  || (regexp_match(
                      dataset.name,
                      ' - ([0-9]+)(st|nd|rd|th)_test$',
                      'i'
                  ))[1]
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE dataset_import_jobs
            SET device_id = NULL
            WHERE source_key = 'nasa-ims-bearings'
            """
        )
    )


def downgrade() -> None:
    # The migration corrects provenance without deleting or rewriting source data.
    pass
