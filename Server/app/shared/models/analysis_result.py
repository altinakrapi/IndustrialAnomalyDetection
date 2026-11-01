from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
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


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    __table_args__ = (
        Index(
            "ix_analysis_results_run_row",
            "analysis_run_id",
            "source_row_number",
            unique=True,
        ),
        Index(
            "uq_analysis_results_run_equipment_measurement",
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
    measurement_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("measurement_records.id", ondelete="SET NULL"), nullable=True
    )
    source_row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    equipment_measurement_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_measurements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_anomaly: Mapped[bool] = mapped_column(Boolean, nullable=False)
    anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    history_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    history_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    history_required: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    feature_values: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    analysis_run = relationship("AnalysisRun", back_populates="results")
    equipment_measurement = relationship("EquipmentMeasurement")
