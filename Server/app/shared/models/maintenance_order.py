
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class MaintenanceOrder(Base):
    __tablename__ = "maintenance_orders"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    anomaly_id: Mapped[int | None] = mapped_column(
        ForeignKey("anomalies.id", ondelete="SET NULL"), nullable=True
    )
    assigned_to: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    priority: Mapped[str] = mapped_column(
        String(30), default="medium", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), default="open", nullable=False
    )
    resolution_notes: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    device = relationship("Device", back_populates="maintenance_orders")
    anomaly = relationship("Anomaly", back_populates="maintenance_orders")
    assignee = relationship("User", foreign_keys=[assigned_to])
    actions = relationship(
        "MaintenanceAction", back_populates="maintenance_order",
        cascade="all, delete-orphan"
    )
