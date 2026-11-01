
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String, Text, desc, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index(
            "uq_notifications_user_dedupe_key",
            "user_id",
            "dedupe_key",
            unique=True,
        ),
        Index(
            "ix_notifications_user_unread_created",
            "user_id",
            "is_read",
            "created_at",
        ),
        Index(
            "ix_notifications_user_type_created",
            "user_id",
            "notification_type",
            "created_at",
        ),
        Index(
            "ix_notifications_user_created_active",
            "user_id",
            desc("created_at"),
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index(
            "ix_notifications_archived_at",
            "archived_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    notification_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_read: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    related_entity_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    related_entity_id: Mapped[int | None] = mapped_column(
        nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
