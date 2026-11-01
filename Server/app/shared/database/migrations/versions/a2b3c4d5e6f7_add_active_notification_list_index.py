"""Add active notification list index.

Revision ID: a2b3c4d5e6f7
Revises: f7a8b9c0d1e2
"""

from alembic import op


revision = "a2b3c4d5e6f7"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_notifications_user_created_active "
        "ON notifications (user_id, created_at DESC) "
        "WHERE archived_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notifications_user_created_active",
        table_name="notifications",
    )
