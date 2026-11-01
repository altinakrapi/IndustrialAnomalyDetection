from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class AlertEpisode(Base):
    __tablename__ = "alert_episodes"
    __table_args__ = (
        Index(
            "uq_alert_episodes_open_parameter",
            "parameter_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index(
            "ix_alert_episodes_escalation_due",
            "status",
            "reviewed_at",
            "escalated_at",
            "first_detected_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parameter_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_parameters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), default="open", server_default="open", nullable=False
    )
    priority: Mapped[str] = mapped_column(
        String(20), default="watch", server_default="watch", nullable=False
    )
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    alert_rules: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    measurement_ids: Mapped[list[int]] = mapped_column(
        JSON, default=list, nullable=False
    )
    last_measurement_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_measurements.id", ondelete="SET NULL"), nullable=True
    )
    history_size: Mapped[int] = mapped_column(Integer, nullable=False)
    history_required: Mapped[int] = mapped_column(Integer, nullable=False)
    consecutive_anomalies: Mapped[int] = mapped_column(Integer, nullable=False)
    window_anomalies: Mapped[int] = mapped_column(Integer, nullable=False)
    normal_readings: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    device = relationship("Device")
    parameter = relationship("EquipmentParameter")
    last_measurement = relationship("EquipmentMeasurement")
    anomalies = relationship("Anomaly", back_populates="alert_episode")
