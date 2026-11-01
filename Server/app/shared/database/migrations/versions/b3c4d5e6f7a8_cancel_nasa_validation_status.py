"""Allow cancelled NASA IMS validations.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
"""

from alembic import op


revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE nasa_ims_validations DROP CONSTRAINT ck_nasa_ims_validation_status")
    op.execute(
        "ALTER TABLE nasa_ims_validations ADD CONSTRAINT ck_nasa_ims_validation_status "
        "CHECK (status IN ('queued', 'running', 'cancelled', 'completed', 'failed'))"
    )


def downgrade() -> None:
    op.execute("UPDATE nasa_ims_validations SET status = 'failed' WHERE status = 'cancelled'")
    op.execute("ALTER TABLE nasa_ims_validations DROP CONSTRAINT ck_nasa_ims_validation_status")
    op.execute(
        "ALTER TABLE nasa_ims_validations ADD CONSTRAINT ck_nasa_ims_validation_status "
        "CHECK (status IN ('queued', 'running', 'completed', 'failed'))"
    )
