from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class SimulatorFaultInterval(Base):
    __tablename__ = "simulator_fault_intervals"
    __table_args__ = (
        UniqueConstraint(
            "ground_truth_run_id",
            "fault_id",
            name="uq_simulator_fault_run_fault_id",
        ),
        Index("ix_simulator_fault_run_device", "ground_truth_run_id", "device_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ground_truth_run_id: Mapped[int] = mapped_column(
        ForeignKey("simulator_ground_truth_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fault_id: Mapped[str] = mapped_column(String(120), nullable=False)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False
    )
    parameter_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_parameters.id", ondelete="RESTRICT"), nullable=False
    )
    device_code: Mapped[str] = mapped_column(String(100), nullable=False)
    parameter_name: Mapped[str] = mapped_column(String(100), nullable=False)
    fault_type: Mapped[str] = mapped_column(String(100), nullable=False)
    start_seconds: Mapped[float] = mapped_column(nullable=False)
    end_seconds: Mapped[float] = mapped_column(nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)

    ground_truth_run = relationship(
        "SimulatorGroundTruthRun", back_populates="fault_intervals"
    )
