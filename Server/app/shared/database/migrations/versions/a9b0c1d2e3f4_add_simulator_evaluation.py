"""persist simulator ground truth and evaluations

Revision ID: a9b0c1d2e3f4
Revises: d8e9f0a1b2c3
Create Date: 2026-11-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "simulator_ground_truth_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=100), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("device_ids", sa.JSON(), nullable=False),
        sa.Column("received_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_reading_count", sa.Integer(), nullable=False),
        sa.Column(
            "imported_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('evaluation', 'development')",
            name="ck_simulator_ground_truth_run_purpose",
        ),
        sa.UniqueConstraint("run_id", name="uq_simulator_ground_truth_runs_run_id"),
    )
    op.create_index(
        "ix_simulator_ground_truth_run_seed",
        "simulator_ground_truth_runs",
        ["seed"],
    )
    op.create_index(
        "ix_simulator_ground_truth_runs_id",
        "simulator_ground_truth_runs",
        ["id"],
    )

    op.create_table(
        "simulator_fault_intervals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ground_truth_run_id",
            sa.Integer(),
            sa.ForeignKey("simulator_ground_truth_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fault_id", sa.String(length=120), nullable=False),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "parameter_id",
            sa.Integer(),
            sa.ForeignKey("equipment_parameters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("device_code", sa.String(length=100), nullable=False),
        sa.Column("parameter_name", sa.String(length=100), nullable=False),
        sa.Column("fault_type", sa.String(length=100), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=30), nullable=False),
        sa.UniqueConstraint(
            "ground_truth_run_id",
            "fault_id",
            name="uq_simulator_fault_run_fault_id",
        ),
    )
    op.create_index(
        "ix_simulator_fault_intervals_ground_truth_run_id",
        "simulator_fault_intervals",
        ["ground_truth_run_id"],
    )
    op.create_index(
        "ix_simulator_fault_run_device",
        "simulator_fault_intervals",
        ["ground_truth_run_id", "device_id"],
    )

    op.create_table(
        "simulator_ground_truth_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ground_truth_run_id",
            sa.Integer(),
            sa.ForeignKey("simulator_ground_truth_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "measurement_id",
            sa.Integer(),
            sa.ForeignKey("equipment_measurements.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "parameter_id",
            sa.Integer(),
            sa.ForeignKey("equipment_parameters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("device_code", sa.String(length=100), nullable=False),
        sa.Column("parameter_name", sa.String(length=100), nullable=False),
        sa.Column("simulated_time_seconds", sa.Float(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fault_types", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "ground_truth_run_id",
            "measurement_id",
            name="uq_simulator_ground_truth_run_measurement",
        ),
    )
    op.create_index(
        "ix_simulator_ground_truth_readings_ground_truth_run_id",
        "simulator_ground_truth_readings",
        ["ground_truth_run_id"],
    )
    op.create_index(
        "ix_simulator_ground_truth_readings_device_time",
        "simulator_ground_truth_readings",
        ["ground_truth_run_id", "device_id", "received_at"],
    )

    op.create_table(
        "simulator_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("ground_truth_run_ids", sa.JSON(), nullable=False),
        sa.Column("analysis_run_ids", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.String(length=2000), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('evaluation', 'development')",
            name="ck_simulator_evaluation_purpose",
        ),
    )
    op.create_index(
        "ix_simulator_evaluations_id", "simulator_evaluations", ["id"]
    )


def downgrade() -> None:
    op.drop_index("ix_simulator_evaluations_id", table_name="simulator_evaluations")
    op.drop_table("simulator_evaluations")
    op.drop_index(
        "ix_simulator_ground_truth_readings_device_time",
        table_name="simulator_ground_truth_readings",
    )
    op.drop_index(
        "ix_simulator_ground_truth_readings_ground_truth_run_id",
        table_name="simulator_ground_truth_readings",
    )
    op.drop_table("simulator_ground_truth_readings")
    op.drop_index(
        "ix_simulator_fault_run_device", table_name="simulator_fault_intervals"
    )
    op.drop_index(
        "ix_simulator_fault_intervals_ground_truth_run_id",
        table_name="simulator_fault_intervals",
    )
    op.drop_table("simulator_fault_intervals")
    op.drop_index(
        "ix_simulator_ground_truth_runs_id", table_name="simulator_ground_truth_runs"
    )
    op.drop_index(
        "ix_simulator_ground_truth_run_seed", table_name="simulator_ground_truth_runs"
    )
    op.drop_table("simulator_ground_truth_runs")
