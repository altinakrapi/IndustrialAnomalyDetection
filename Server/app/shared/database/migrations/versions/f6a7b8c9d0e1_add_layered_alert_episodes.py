"""add layered alert configuration and episode history

Revision ID: f6a7b8c9d0e1
Revises: c3d4e5f6a7b8
Create Date: 2026-10-27 21:50:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "equipment_parameters",
        sa.Column("consecutive_anomaly_threshold", sa.Integer(), nullable=True),
    )
    op.add_column(
        "equipment_parameters",
        sa.Column("persistence_window_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "equipment_parameters",
        sa.Column("persistence_anomaly_threshold", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_equipment_parameter_consecutive_alert_range",
        "equipment_parameters",
        "consecutive_anomaly_threshold IS NULL OR "
        "consecutive_anomaly_threshold BETWEEN 1 AND 100",
    )
    op.create_check_constraint(
        "ck_equipment_parameter_persistence_window_range",
        "equipment_parameters",
        "persistence_window_size IS NULL OR persistence_window_size BETWEEN 1 AND 100",
    )
    op.create_check_constraint(
        "ck_equipment_parameter_persistence_count_range",
        "equipment_parameters",
        "persistence_anomaly_threshold IS NULL OR "
        "persistence_anomaly_threshold BETWEEN 1 AND 100",
    )
    op.create_table(
        "alert_episodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("parameter_id", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="open", nullable=False
        ),
        sa.Column(
            "priority", sa.String(length=20), server_default="watch", nullable=False
        ),
        sa.Column("severity", sa.String(length=30), nullable=False),
        sa.Column(
            "alert_rules", sa.JSON(), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column(
            "measurement_ids",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("last_measurement_id", sa.Integer(), nullable=True),
        sa.Column("history_size", sa.Integer(), nullable=False),
        sa.Column("history_required", sa.Integer(), nullable=False),
        sa.Column("consecutive_anomalies", sa.Integer(), nullable=False),
        sa.Column("window_anomalies", sa.Integer(), nullable=False),
        sa.Column("normal_readings", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parameter_id"], ["equipment_parameters.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["last_measurement_id"],
            ["equipment_measurements.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_alert_episodes_device_id", "alert_episodes", ["device_id"]
    )
    op.create_index(
        "ix_alert_episodes_parameter_id", "alert_episodes", ["parameter_id"]
    )
    op.create_index(
        "uq_alert_episodes_open_parameter",
        "alert_episodes",
        ["parameter_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_alert_episodes_escalation_due",
        "alert_episodes",
        ["status", "reviewed_at", "escalated_at", "first_detected_at"],
    )
    op.add_column(
        "anomalies",
        sa.Column("alert_episode_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "anomalies",
        sa.Column(
            "alert_rules", sa.JSON(), server_default=sa.text("'[]'"), nullable=False
        ),
    )
    op.add_column(
        "anomalies", sa.Column("alert_priority", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "anomalies", sa.Column("history_state", sa.String(length=30), nullable=True)
    )
    op.add_column(
        "anomalies", sa.Column("history_size", sa.Integer(), nullable=True)
    )
    op.add_column(
        "anomalies", sa.Column("history_required", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "anomalies_alert_episode_id_fkey",
        "anomalies",
        "alert_episodes",
        ["alert_episode_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_anomalies_alert_episode_id", "anomalies", ["alert_episode_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_anomalies_alert_episode_id", table_name="anomalies")
    op.drop_constraint(
        "anomalies_alert_episode_id_fkey", "anomalies", type_="foreignkey"
    )
    op.drop_column("anomalies", "history_required")
    op.drop_column("anomalies", "history_size")
    op.drop_column("anomalies", "history_state")
    op.drop_column("anomalies", "alert_priority")
    op.drop_column("anomalies", "alert_rules")
    op.drop_column("anomalies", "alert_episode_id")
    op.drop_index("ix_alert_episodes_escalation_due", table_name="alert_episodes")
    op.drop_index(
        "uq_alert_episodes_open_parameter", table_name="alert_episodes"
    )
    op.drop_index("ix_alert_episodes_parameter_id", table_name="alert_episodes")
    op.drop_index("ix_alert_episodes_device_id", table_name="alert_episodes")
    op.drop_table("alert_episodes")
    op.drop_constraint(
        "ck_equipment_parameter_persistence_count_range",
        "equipment_parameters",
        type_="check",
    )
    op.drop_constraint(
        "ck_equipment_parameter_persistence_window_range",
        "equipment_parameters",
        type_="check",
    )
    op.drop_constraint(
        "ck_equipment_parameter_consecutive_alert_range",
        "equipment_parameters",
        type_="check",
    )
    op.drop_column("equipment_parameters", "persistence_anomaly_threshold")
    op.drop_column("equipment_parameters", "persistence_window_size")
    op.drop_column("equipment_parameters", "consecutive_anomaly_threshold")
