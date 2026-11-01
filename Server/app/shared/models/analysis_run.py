
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("uq_analysis_runs_execution_key", "execution_key", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    equipment_measurement_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_measurements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    model_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="RESTRICT"), nullable=True
    )
    preprocessing_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("preprocessing_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False
    )
    execution_type: Mapped[str] = mapped_column(
        String(30), default="manual", nullable=False
    )
    source_type: Mapped[str] = mapped_column(
        String(30), default="dataset", nullable=False
    )
    execution_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    source_start_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_end_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_records: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    anomalies_found: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    metrics: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    dataset = relationship("Dataset", back_populates="analysis_runs")
    device = relationship("Device")
    equipment_measurement = relationship("EquipmentMeasurement")
    model_config = relationship("ModelConfig", back_populates="analysis_runs")
    results = relationship(
        "AnalysisResult", back_populates="analysis_run", cascade="all, delete-orphan"
    )
    anomalies = relationship(
        "Anomaly", back_populates="analysis_run",
        cascade="all, delete-orphan"
    )
