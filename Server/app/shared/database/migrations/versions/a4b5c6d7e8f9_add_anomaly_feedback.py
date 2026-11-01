"""Add immutable anomaly human feedback history.

Revision ID: a4b5c6d7e8f9
Revises: e8f9a0b1c2d3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "e8f9a0b1c2d3"
depends_on: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "anomaly_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("anomaly_id", sa.Integer(), sa.ForeignKey("anomalies.id", ondelete="CASCADE"), nullable=True),
        sa.Column("alert_episode_id", sa.Integer(), sa.ForeignKey("alert_episodes.id", ondelete="CASCADE"), nullable=True),
        sa.Column("label", sa.String(length=30), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("maintenance_order_id", sa.Integer(), sa.ForeignKey("maintenance_orders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(anomaly_id IS NOT NULL AND alert_episode_id IS NULL) OR "
            "(anomaly_id IS NULL AND alert_episode_id IS NOT NULL)",
            name="ck_anomaly_feedback_exact_target",
        ),
        sa.CheckConstraint(
            "label IN ('confirmed_fault', 'false_alarm', 'needs_more_data')",
            name="ck_anomaly_feedback_label",
        ),
    )
    op.create_index("ix_anomaly_feedback_anomaly_id", "anomaly_feedback", ["anomaly_id"])
    op.create_index("ix_anomaly_feedback_alert_episode_id", "anomaly_feedback", ["alert_episode_id"])
    op.create_index("ix_anomaly_feedback_id", "anomaly_feedback", ["id"])
    op.create_index(
        "uq_anomaly_feedback_anomaly",
        "anomaly_feedback",
        ["anomaly_id"],
        unique=True,
        postgresql_where=sa.text("anomaly_id IS NOT NULL"),
    )
    op.create_index(
        "uq_anomaly_feedback_episode",
        "anomaly_feedback",
        ["alert_episode_id"],
        unique=True,
        postgresql_where=sa.text("alert_episode_id IS NOT NULL"),
    )

    op.create_table(
        "anomaly_feedback_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("feedback_id", sa.Integer(), sa.ForeignKey("anomaly_feedback.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(length=30), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("maintenance_order_id", sa.Integer(), sa.ForeignKey("maintenance_orders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "label IN ('confirmed_fault', 'false_alarm', 'needs_more_data')",
            name="ck_anomaly_feedback_history_label",
        ),
    )
    op.create_index("ix_anomaly_feedback_history_feedback_id", "anomaly_feedback_history", ["feedback_id"])
    op.create_index("ix_anomaly_feedback_history_id", "anomaly_feedback_history", ["id"])


def downgrade() -> None:
    op.drop_table("anomaly_feedback_history")
    op.drop_table("anomaly_feedback")
