"""Add registered-equipment measurements to shared analysis history.

Revision ID: c3f54a71be20
Revises: 61b6d2a4e8c1
Create Date: 2026-10-26 20:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f54a71be20"
down_revision: Union[str, None] = "61b6d2a4e8c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "equipment_parameters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("unit", sa.String(length=30), nullable=False),
        sa.Column("lower_limit", sa.Float(), nullable=True),
        sa.Column("upper_limit", sa.Float(), nullable=True),
        sa.Column("warning_low", sa.Float(), nullable=True),
        sa.Column("warning_high", sa.Float(), nullable=True),
        sa.Column("critical_low", sa.Float(), nullable=True),
        sa.Column("critical_high", sa.Float(), nullable=True),
        sa.Column("expected_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("analysis_method", sa.String(length=30), nullable=False),
        sa.Column("z_score_threshold", sa.Float(), nullable=False),
        sa.Column("isolation_contamination", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expected_interval_minutes > 0",
            name="ck_equipment_parameter_interval_positive",
        ),
        sa.CheckConstraint(
            "z_score_threshold > 0",
            name="ck_equipment_parameter_z_threshold_positive",
        ),
        sa.CheckConstraint(
            "isolation_contamination > 0 AND isolation_contamination <= 0.5",
            name="ck_equipment_parameter_contamination_range",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "device_id", "name", name="uq_equipment_parameter_device_name"
        ),
    )
    op.create_index(
        "ix_equipment_parameters_device_id",
        "equipment_parameters",
        ["device_id"],
        unique=False,
    )

    op.create_table(
        "equipment_measurements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parameter_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=30), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("recorded_by", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["parameter_id"],
            ["equipment_parameters.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["recorded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "parameter_id",
            "measured_at",
            name="uq_equipment_measurement_parameter_timestamp",
        ),
        sa.UniqueConstraint(
            "parameter_id",
            "idempotency_key",
            name="uq_equipment_measurement_idempotency_key",
        ),
    )
    op.create_index(
        "ix_equipment_measurements_parameter_id",
        "equipment_measurements",
        ["parameter_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_measurement_parameter_measured_at",
        "equipment_measurements",
        ["parameter_id", "measured_at"],
        unique=False,
    )

    op.drop_constraint(
        "analysis_runs_dataset_id_fkey", "analysis_runs", type_="foreignkey"
    )
    op.alter_column(
        "analysis_runs",
        "dataset_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_foreign_key(
        "analysis_runs_dataset_id_fkey",
        "analysis_runs",
        "datasets",
        ["dataset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.alter_column(
        "analysis_runs",
        "model_config_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "analysis_runs",
        sa.Column("device_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "analysis_runs_device_id_fkey",
        "analysis_runs",
        "devices",
        ["device_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_analysis_runs_device_id", "analysis_runs", ["device_id"], unique=False
    )
    op.add_column(
        "analysis_runs",
        sa.Column(
            "source_type",
            sa.String(length=30),
            server_default="dataset",
            nullable=False,
        ),
    )

    op.alter_column(
        "analysis_results",
        "source_row_number",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "analysis_results",
        sa.Column("equipment_measurement_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "analysis_results_equipment_measurement_id_fkey",
        "analysis_results",
        "equipment_measurements",
        ["equipment_measurement_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_analysis_results_equipment_measurement_id",
        "analysis_results",
        ["equipment_measurement_id"],
        unique=False,
    )
    op.create_index(
        "uq_analysis_results_run_equipment_measurement",
        "analysis_results",
        ["analysis_run_id", "equipment_measurement_id"],
        unique=True,
        postgresql_where=sa.text("equipment_measurement_id IS NOT NULL"),
    )

    op.add_column(
        "anomalies",
        sa.Column("equipment_measurement_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "anomalies_equipment_measurement_id_fkey",
        "anomalies",
        "equipment_measurements",
        ["equipment_measurement_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_anomalies_analysis_equipment_measurement",
        "anomalies",
        ["analysis_run_id", "equipment_measurement_id"],
        unique=True,
        postgresql_where=sa.text("equipment_measurement_id IS NOT NULL"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_equipment_runs = connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM analysis_runs WHERE source_type = 'equipment')")
    )
    has_equipment_data = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM equipment_measurements) "
            "OR EXISTS (SELECT 1 FROM equipment_parameters)"
        )
    )
    if has_equipment_runs or has_equipment_data:
        raise RuntimeError(
            "Cannot remove equipment analysis support while equipment execution history exists."
        )

    op.drop_index(
        "uq_anomalies_analysis_equipment_measurement", table_name="anomalies"
    )
    op.drop_constraint(
        "anomalies_equipment_measurement_id_fkey",
        "anomalies",
        type_="foreignkey",
    )
    op.drop_column("anomalies", "equipment_measurement_id")

    op.drop_index(
        "uq_analysis_results_run_equipment_measurement",
        table_name="analysis_results",
    )
    op.drop_index(
        "ix_analysis_results_equipment_measurement_id",
        table_name="analysis_results",
    )
    op.drop_constraint(
        "analysis_results_equipment_measurement_id_fkey",
        "analysis_results",
        type_="foreignkey",
    )
    op.drop_column("analysis_results", "equipment_measurement_id")
    op.alter_column(
        "analysis_results",
        "source_row_number",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.drop_column("analysis_runs", "source_type")
    op.drop_index("ix_analysis_runs_device_id", table_name="analysis_runs")
    op.drop_constraint(
        "analysis_runs_device_id_fkey", "analysis_runs", type_="foreignkey"
    )
    op.drop_column("analysis_runs", "device_id")
    op.alter_column(
        "analysis_runs",
        "model_config_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_constraint(
        "analysis_runs_dataset_id_fkey", "analysis_runs", type_="foreignkey"
    )
    op.alter_column(
        "analysis_runs", "dataset_id", existing_type=sa.Integer(), nullable=False
    )
    op.create_foreign_key(
        "analysis_runs_dataset_id_fkey",
        "analysis_runs",
        "datasets",
        ["dataset_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_index(
        "ix_equipment_measurement_parameter_measured_at",
        table_name="equipment_measurements",
    )
    op.drop_index(
        "ix_equipment_measurements_parameter_id",
        table_name="equipment_measurements",
    )
    op.drop_table("equipment_measurements")
    op.drop_index("ix_equipment_parameters_device_id", table_name="equipment_parameters")
    op.drop_table("equipment_parameters")
