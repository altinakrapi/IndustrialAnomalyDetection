
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class PreprocessingRun(Base):
    __tablename__ = "preprocessing_runs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    config_id: Mapped[int | None] = mapped_column(
        ForeignKey("preprocessing_configs.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False
    )
    original_row_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    processed_row_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    dataset = relationship("Dataset", back_populates="preprocessing_runs")
