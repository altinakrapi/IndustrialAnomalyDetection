
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class Anomaly(Base):
    __tablename__ = "anomalies"
    __table_args__ = (
        Index(
            "uq_anomalies_analysis_measurement",
            "analysis_run_id",
            "measurement_record_id",
            unique=True,
            postgresql_where=text("measurement_record_id IS NOT NULL"),
        ),
        Index(
            "uq_anomalies_analysis_equipment_measurement",
            "analysis_run_id",
            "equipment_measurement_id",
            unique=True,
            postgresql_where=text("equipment_measurement_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    measurement_id: Mapped[int | None] = mapped_column(
        ForeignKey("sensor_measurements.id", ondelete="SET NULL"),
        nullable=True,
    )
    measurement_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("measurement_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    equipment_measurement_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_measurements.id", ondelete="SET NULL"),
        nullable=True,
    )
    anomaly_type: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    anomaly_score: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    severity: Mapped[str] = mapped_column(
        String(30), default="medium", nullable=False
    )
    computed_severity: Mapped[str | None] = mapped_column(String(30), nullable=True)
    severity_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(30), default="new", nullable=False
    )
    observed_value: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    expected_range: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    feature_values: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    alert_episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_episodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    alert_rules: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    alert_priority: Mapped[str | None] = mapped_column(String(20), nullable=True)
    history_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    history_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    history_required: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    analysis_run = relationship("AnalysisRun", back_populates="anomalies")
    measurement_record = relationship("MeasurementRecord")
    equipment_measurement = relationship("EquipmentMeasurement")
    alert_episode = relationship("AlertEpisode", back_populates="anomalies")
    reviews = relationship(
        "AnomalyReview", back_populates="anomaly",
        cascade="all, delete-orphan"
    )
    maintenance_orders = relationship(
        "MaintenanceOrder", back_populates="anomaly"
    )
