from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class NasaIMSValidation(Base):
    __tablename__ = "nasa_ims_validations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'cancelled', 'completed', 'failed')",
            name="ck_nasa_ims_validation_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False)
    analysis_run_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    limitations: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
