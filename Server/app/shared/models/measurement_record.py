from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import Base


class MeasurementRecord(Base):
    __tablename__ = "measurement_records"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id", "source_row_number",
            name="uq_measurement_record_dataset_row"
        ),
        Index("ix_measurement_record_dataset_timestamp", "dataset_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    values: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_labeled_anomaly: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )

    dataset = relationship("Dataset", back_populates="measurement_records")
