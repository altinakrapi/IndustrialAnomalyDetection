from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class AnomalyFeedback(Base):
    __tablename__ = "anomaly_feedback"
    __table_args__ = (
        Index(
            "uq_anomaly_feedback_anomaly",
            "anomaly_id",
            unique=True,
            postgresql_where=text("anomaly_id IS NOT NULL"),
        ),
        Index(
            "uq_anomaly_feedback_episode",
            "alert_episode_id",
            unique=True,
            postgresql_where=text("alert_episode_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    anomaly_id: Mapped[int | None] = mapped_column(
        ForeignKey("anomalies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    alert_episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_episodes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(30), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    maintenance_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("maintenance_orders.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    creator = relationship("User", foreign_keys=[created_by])
    maintenance_order = relationship("MaintenanceOrder", foreign_keys=[maintenance_order_id])
    history = relationship(
        "AnomalyFeedbackHistory",
        back_populates="feedback",
        cascade="all, delete-orphan",
        order_by="AnomalyFeedbackHistory.changed_at",
    )
