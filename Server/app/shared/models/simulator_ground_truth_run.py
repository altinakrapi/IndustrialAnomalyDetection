from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class SimulatorGroundTruthRun(Base):
    __tablename__ = "simulator_ground_truth_runs"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('evaluation', 'development')",
            name="ck_simulator_ground_truth_run_purpose",
        ),
        UniqueConstraint("run_id", name="uq_simulator_ground_truth_runs_run_id"),
        Index("ix_simulator_ground_truth_run_seed", "seed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    run_id: Mapped[str] = mapped_column(String(100), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    device_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    received_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_before: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_reading_count: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    fault_intervals = relationship(
        "SimulatorFaultInterval",
        back_populates="ground_truth_run",
        cascade="all, delete-orphan",
    )
    readings = relationship(
        "SimulatorGroundTruthReading",
        back_populates="ground_truth_run",
        cascade="all, delete-orphan",
    )
