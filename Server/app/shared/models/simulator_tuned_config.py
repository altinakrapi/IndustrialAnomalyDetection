from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class SimulatorTunedConfig(Base):
    __tablename__ = "simulator_tuned_configs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tuning_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("simulator_tuning_runs.id", ondelete="SET NULL"), nullable=True
    )
    model_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    algorithm: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    objective: Mapped[str] = mapped_column(String(100), nullable=False)
    false_alarm_cap: Mapped[float] = mapped_column(nullable=False)
    development_run_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    development_seeds: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    limitations: Mapped[str] = mapped_column(String(4000), nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_superseded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    superseded_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
