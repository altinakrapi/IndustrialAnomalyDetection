from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class SimulatorGroundTruthReading(Base):
    __tablename__ = "simulator_ground_truth_readings"
    __table_args__ = (
        UniqueConstraint(
            "ground_truth_run_id",
            "measurement_id",
            name="uq_simulator_ground_truth_run_measurement",
        ),
        Index(
            "ix_simulator_ground_truth_readings_device_time",
            "ground_truth_run_id",
            "device_id",
            "received_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ground_truth_run_id: Mapped[int] = mapped_column(
        ForeignKey("simulator_ground_truth_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    measurement_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_measurements.id", ondelete="RESTRICT"),
        nullable=False,
    )
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False
    )
    parameter_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_parameters.id", ondelete="RESTRICT"), nullable=False
    )
    device_code: Mapped[str] = mapped_column(String(100), nullable=False)
    parameter_name: Mapped[str] = mapped_column(String(100), nullable=False)
    simulated_time_seconds: Mapped[float] = mapped_column(nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fault_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    ground_truth_run = relationship(
        "SimulatorGroundTruthRun", back_populates="readings"
    )
