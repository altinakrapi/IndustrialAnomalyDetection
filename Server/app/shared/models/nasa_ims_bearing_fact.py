from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class NasaIMSBearingFact(Base):
    __tablename__ = "nasa_ims_bearing_facts"
    __table_args__ = (
        UniqueConstraint("test_set", "bearing_number", name="uq_nasa_ims_fact_set_bearing"),
        Index("ix_nasa_ims_bearing_fact_test_set", "test_set"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    test_set: Mapped[str] = mapped_column(String(20), nullable=False)
    bearing_number: Mapped[int] = mapped_column(Integer, nullable=False)
    channel_numbers: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    is_failed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    healthy_bearing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_bearing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_document: Mapped[str] = mapped_column(String(255), nullable=False)
    source_section: Mapped[str] = mapped_column(String(100), nullable=False)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
