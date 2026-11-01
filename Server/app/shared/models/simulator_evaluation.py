from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class SimulatorEvaluation(Base):
    __tablename__ = "simulator_evaluations"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('evaluation', 'development')",
            name="ck_simulator_evaluation_purpose",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False)
    ground_truth_run_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    analysis_run_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    limitations: Mapped[str] = mapped_column(String(2000), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
