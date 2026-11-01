"""Add notification archive and query indexes.

Revision ID: f7a8b9c0d1e2
Revises: b1c2d3e4f5a6
"""

from alembic import op
import sqlalchemy as sa


revision = "f7a8b9c0d1e2"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_notifications_user_unread_created",
        "notifications",
        ["user_id", "is_read", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_user_type_created",
        "notifications",
        ["user_id", "notification_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_archived_at",
        "notifications",
        ["archived_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_archived_at", table_name="notifications")
    op.drop_index("ix_notifications_user_type_created", table_name="notifications")
    op.drop_index("ix_notifications_user_unread_created", table_name="notifications")
    op.drop_column("notifications", "archived_at")
