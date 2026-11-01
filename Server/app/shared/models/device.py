
from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    device_code: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    device_type: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(150), nullable=True)
    model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(150), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    installation_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="active", nullable=False
    )
    source_type: Mapped[str] = mapped_column(
        String(30), default="manual", server_default="manual", nullable=False
    )
    is_registered: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    responsible_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    datasets = relationship("Dataset", back_populates="device")
    sensor_api_keys = relationship(
        "DeviceAPIKey", back_populates="device", cascade="all, delete-orphan"
    )
    responsible_user = relationship("User", foreign_keys=[responsible_user_id])
    maintenance_orders = relationship(
        "MaintenanceOrder", back_populates="device"
    )
