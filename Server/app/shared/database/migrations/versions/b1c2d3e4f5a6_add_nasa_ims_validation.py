"""persist verified NASA IMS bearing facts and validation reports

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-10-28 23:11:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


SOURCE_DOCUMENT = "Readme Document for IMS Bearing Data.pdf"


def _fact_rows() -> list[dict]:
    set_data = {
        "1st_test": {
            "channels": {1: [1, 2], 2: [3, 4], 3: [5, 6], 4: [7, 8]},
            "failed": {3: "unspecified", 4: "roller_element"},
            "healthy": 2,
            "excerpt": (
                "At the end of the test-to-failure experiment, bearing 3 and "
                "roller element defect in bearing 4."
            ),
        },
        "2nd_test": {
            "channels": {1: [1], 2: [2], 3: [3], 4: [4]},
            "failed": {1: "outer_race"},
            "healthy": 3,
            "excerpt": (
                "At the end of the test-to-failure experiment, outer race "
                "failure occurred in bearing 1."
            ),
        },
        "3rd_test": {
            "channels": {1: [1], 2: [2], 3: [3], 4: [4]},
            "failed": {3: "unspecified"},
            "healthy": 3,
            "excerpt": (
                "At the end of the test-to-failure experiment, bearing 3."
            ),
        },
    }
    return [
        {
            "test_set": test_set,
            "bearing_number": bearing_number,
            "channel_numbers": channels,
            "is_failed": bearing_number in values["failed"],
            "failure_type": values["failed"].get(bearing_number),
            "healthy_bearing_count": values["healthy"],
            "failed_bearing_count": len(values["failed"]),
            "source_document": SOURCE_DOCUMENT,
            "source_section": f"Data Structure / Set No. {test_set[0]}",
            "source_excerpt": values["excerpt"],
        }
        for test_set, values in set_data.items()
        for bearing_number, channels in values["channels"].items()
    ]


def upgrade() -> None:
    op.create_table(
        "nasa_ims_bearing_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("test_set", sa.String(length=20), nullable=False),
        sa.Column("bearing_number", sa.Integer(), nullable=False),
        sa.Column("channel_numbers", sa.JSON(), nullable=False),
        sa.Column("is_failed", sa.Boolean(), nullable=False),
        sa.Column("failure_type", sa.String(length=50), nullable=True),
        sa.Column("healthy_bearing_count", sa.Integer(), nullable=False),
        sa.Column("failed_bearing_count", sa.Integer(), nullable=False),
        sa.Column("source_document", sa.String(length=255), nullable=False),
        sa.Column("source_section", sa.String(length=100), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "test_set", "bearing_number", name="uq_nasa_ims_fact_set_bearing"
        ),
    )
    op.create_index(
        "ix_nasa_ims_bearing_facts_id", "nasa_ims_bearing_facts", ["id"]
    )
    op.create_index(
        "ix_nasa_ims_bearing_fact_test_set",
        "nasa_ims_bearing_facts",
        ["test_set"],
    )
    facts = sa.table(
        "nasa_ims_bearing_facts",
        sa.column("test_set", sa.String()),
        sa.column("bearing_number", sa.Integer()),
        sa.column("channel_numbers", sa.JSON()),
        sa.column("is_failed", sa.Boolean()),
        sa.column("failure_type", sa.String()),
        sa.column("healthy_bearing_count", sa.Integer()),
        sa.column("failed_bearing_count", sa.Integer()),
        sa.column("source_document", sa.String()),
        sa.column("source_section", sa.String()),
        sa.column("source_excerpt", sa.Text()),
    )
    op.bulk_insert(facts, _fact_rows())

    op.create_table(
        "nasa_ims_validations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.Integer(),
            sa.ForeignKey("datasets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("analysis_run_ids", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_nasa_ims_validation_status",
        ),
    )
    op.create_index(
        "ix_nasa_ims_validations_id", "nasa_ims_validations", ["id"]
    )
    op.create_index(
        "ix_nasa_ims_validations_dataset_id", "nasa_ims_validations", ["dataset_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_nasa_ims_validations_dataset_id", table_name="nasa_ims_validations")
    op.drop_index("ix_nasa_ims_validations_id", table_name="nasa_ims_validations")
    op.drop_table("nasa_ims_validations")
    op.drop_index(
        "ix_nasa_ims_bearing_fact_test_set", table_name="nasa_ims_bearing_facts"
    )
    op.drop_index(
        "ix_nasa_ims_bearing_facts_id", table_name="nasa_ims_bearing_facts"
    )
    op.drop_table("nasa_ims_bearing_facts")
