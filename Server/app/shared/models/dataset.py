
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_format: Mapped[str] = mapped_column(
        String(20), default="csv", nullable=False
    )
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_row_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    column_metadata: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50), default="uploaded", nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    device = relationship("Device", back_populates="datasets")
    measurements = relationship(
        "SensorMeasurement", back_populates="dataset",
        cascade="all, delete-orphan"
    )
    measurement_records = relationship(
        "MeasurementRecord", back_populates="dataset",
        cascade="all, delete-orphan"
    )
    preprocessing_runs = relationship(
        "PreprocessingRun", back_populates="dataset"
    )
    analysis_runs = relationship("AnalysisRun", back_populates="dataset")
