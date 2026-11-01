
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class AnomalyReview(Base):
    __tablename__ = "anomaly_reviews"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    anomaly_id: Mapped[int] = mapped_column(
        ForeignKey("anomalies.id", ondelete="CASCADE"), nullable=False
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decision: Mapped[str] = mapped_column(
        String(50), nullable=False
    )
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    anomaly = relationship("Anomaly", back_populates="reviews")
