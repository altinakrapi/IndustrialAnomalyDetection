from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class EquipmentMeasurement(Base):
    __tablename__ = "equipment_measurements"
    __table_args__ = (
        UniqueConstraint(
            "parameter_id",
            "measured_at",
            name="uq_equipment_measurement_parameter_timestamp",
        ),
        UniqueConstraint(
            "parameter_id",
            "idempotency_key",
            name="uq_equipment_measurement_idempotency_key",
        ),
        Index(
            "ix_equipment_measurement_parameter_measured_at",
            "parameter_id",
            "measured_at",
        ),
        Index(
            "ix_equipment_measurements_source_received_at",
            "source_type",
            "received_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    parameter_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_parameters.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sensor_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    recorded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(
        String(30), default="manual", nullable=False
    )
    source_type: Mapped[str] = mapped_column(
        String(30), default="manual", server_default="manual", nullable=False
    )
    sensor_api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("device_api_keys.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    measurement_severity: Mapped[str | None] = mapped_column(String(30), nullable=True)
    measurement_severity_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    measurement_detectors: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    measurement_agreeing_detectors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measurement_rule_fired: Mapped[str | None] = mapped_column(String(100), nullable=True)
    measurement_analysis_run_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    measurement_severity_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    parameter = relationship("EquipmentParameter", back_populates="measurements")
    recorder = relationship("User")
    sensor_api_key = relationship("DeviceAPIKey")
