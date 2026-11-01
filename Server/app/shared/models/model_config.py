
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Float, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    parameters: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    contamination: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(
        "AnalysisRun", back_populates="model_config"
    )
