from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class EquipmentParameter(Base):
    __tablename__ = "equipment_parameters"
    __table_args__ = (
        UniqueConstraint("device_id", "name", name="uq_equipment_parameter_device_name"),
        CheckConstraint(
            "expected_interval_minutes > 0",
            name="ck_equipment_parameter_interval_positive",
        ),
        CheckConstraint(
            "expected_interval_seconds IS NULL OR expected_interval_seconds > 0",
            name="ck_equipment_parameter_interval_seconds_positive",
        ),
        CheckConstraint(
            "z_score_threshold > 0",
            name="ck_equipment_parameter_z_threshold_positive",
        ),
        CheckConstraint(
            "isolation_contamination > 0 AND isolation_contamination <= 0.5",
            name="ck_equipment_parameter_contamination_range",
        ),
        CheckConstraint(
            "consecutive_anomaly_threshold IS NULL OR "
            "consecutive_anomaly_threshold BETWEEN 1 AND 100",
            name="ck_equipment_parameter_consecutive_alert_range",
        ),
        CheckConstraint(
            "persistence_window_size IS NULL OR persistence_window_size BETWEEN 1 AND 100",
            name="ck_equipment_parameter_persistence_window_range",
        ),
        CheckConstraint(
            "persistence_anomaly_threshold IS NULL OR "
            "persistence_anomaly_threshold BETWEEN 1 AND 100",
            name="ck_equipment_parameter_persistence_count_range",
        ),
        CheckConstraint(
            "flatline_reading_count BETWEEN 2 AND 100",
            name="ck_equipment_parameter_flatline_reading_count_range",
        ),
        CheckConstraint(
            "flatline_variance_tolerance > 0 AND "
            "flatline_variance_tolerance <= 1",
            name="ck_equipment_parameter_flatline_tolerance_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    lower_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_interval_minutes: Mapped[int] = mapped_column(
        Integer, default=120, server_default="120", nullable=False
    )
    expected_interval_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    analysis_method: Mapped[str] = mapped_column(
        String(30),
        default="isolation_forest",
        server_default="isolation_forest",
        nullable=False,
    )
    z_score_threshold: Mapped[float] = mapped_column(
        Float, default=3.0, nullable=False
    )
    isolation_contamination: Mapped[float] = mapped_column(
        Float, default=0.05, nullable=False
    )
    consecutive_anomaly_threshold: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    persistence_window_size: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    persistence_anomaly_threshold: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    flatline_reading_count: Mapped[int] = mapped_column(
        Integer, default=10, server_default="10", nullable=False
    )
    flatline_variance_tolerance: Mapped[float] = mapped_column(
        Float, default=0.05, server_default="0.05", nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
    measurements = relationship(
        "EquipmentMeasurement",
        back_populates="parameter",
        cascade="all, delete-orphan",
    )
