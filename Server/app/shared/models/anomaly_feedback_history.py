from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class AnomalyFeedbackHistory(Base):
    __tablename__ = "anomaly_feedback_history"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    feedback_id: Mapped[int] = mapped_column(
        ForeignKey("anomaly_feedback.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(30), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    maintenance_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("maintenance_orders.id", ondelete="SET NULL"), nullable=True
    )
    changed_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    feedback = relationship("AnomalyFeedback", back_populates="history")
    changer = relationship("User", foreign_keys=[changed_by])
