from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class SimulatorTuningRun(Base):
    __tablename__ = "simulator_tuning_runs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="completed", nullable=False)
    objective: Mapped[str] = mapped_column(String(100), nullable=False)
    false_alarm_cap: Mapped[float] = mapped_column(Float, nullable=False)
    ground_truth_run_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    data_seeds: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False)
    candidates: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    runtime_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    limitations: Mapped[str] = mapped_column(String(4000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
