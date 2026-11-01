from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class MaintenanceAction(Base):
    __tablename__ = "maintenance_actions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    maintenance_order_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    performed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action_description: Mapped[str] = mapped_column(Text, nullable=False)
    findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendations: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_confirmed: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    maintenance_order = relationship(
        "MaintenanceOrder", back_populates="actions"
    )
