from itertools import islice
from typing import Iterable
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.orm import Session

from app.shared.models import Notification, User

NOTIFICATION_INSERT_BATCH_SIZE = 2000


def notify_user_rate_limited(
    db: Session,
    *,
    user_id: int,
    device_id: int,
    limit: int,
    title: str,
    message: str,
    notification_type: str,
    details: dict,
    entity_type: str = "anomaly",
    entity_id: int | None = None,
    dedupe_key: str,
) -> bool:
    now = datetime.now(timezone.utc)
    hour_key = now.strftime("%Y%m%d%H")
    scope_prefix = f"device-rate:{device_id}:{hour_key}:{user_id}:"
    hourly_count = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.dedupe_key.like(f"{scope_prefix}%"),
            Notification.created_at >= now.replace(minute=0, second=0, microsecond=0),
        )
    ) or 0
    if hourly_count >= limit:
        aggregate_key = f"{scope_prefix}summary"
        aggregate = db.scalar(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.dedupe_key == aggregate_key,
            )
        )
        suppressed = (aggregate.details.get("suppressed_count", 0) + 1) if aggregate else 1
        if aggregate is None:
            db.add(
                Notification(
                    user_id=user_id,
                    title="Notification rate limit reached",
                    message=f"{limit} notifications suppressed for device #{device_id} this hour.",
                    notification_type="notification_suppressed",
                    details={**details, "suppressed_count": suppressed},
                    dedupe_key=aggregate_key,
                )
            )
        else:
            aggregate.details = {**aggregate.details, "suppressed_count": suppressed}
            aggregate.message = (
                f"{suppressed} notifications suppressed for device #{device_id} this hour."
            )
        return False
    notify_user(
        db,
        user_id,
        title,
        message,
        notification_type,
        entity_type,
        entity_id,
        details,
        f"{scope_prefix}{dedupe_key}",
    )
    return True


def notify_user(
    db: Session,
    user_id: int | None,
    title: str,
    message: str,
    notification_type: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict | None = None,
    dedupe_key: str | None = None,
) -> None:
    if user_id is None:
        return
    details = dict(details or {})
    device_id = details.pop("rate_limit_device_id", None)
    if device_id is not None:
        from app.core.system_settings import get_system_setting

        limit = get_system_setting(db, "notification_device_hourly_limit", 20)
        if isinstance(limit, int):
            notify_user_rate_limited(
                db,
                user_id=user_id,
                device_id=int(device_id),
                limit=limit,
                title=title,
                message=message,
                notification_type=notification_type,
                details=details,
                entity_type=entity_type,
                entity_id=entity_id,
                dedupe_key=dedupe_key or "",
            )
            return
    if dedupe_key is not None:
        db.execute(
            postgres_insert(Notification)
            .values(
                user_id=user_id,
                title=title,
                message=message,
                notification_type=notification_type,
                related_entity_type=entity_type,
                related_entity_id=entity_id,
                details=details or {},
                dedupe_key=dedupe_key,
                is_read=False,
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "dedupe_key"]
            )
        )
        return
    db.add(
        Notification(
            user_id=user_id,
            title=title,
            message=message,
            notification_type=notification_type,
            related_entity_type=entity_type,
            related_entity_id=entity_id,
            details=details or {},
        )
    )


def notify_roles(
    db: Session,
    roles: set[str],
    title: str,
    message: str,
    notification_type: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    exclude_user_id: int | None = None,
    details: dict | None = None,
    dedupe_key: str | None = None,
) -> None:
    for user_id in db.scalars(
        select(User.id).where(User.is_active.is_(True), User.role.in_(roles))
    ):
        if user_id == exclude_user_id:
            continue
        notify_user(
            db,
            user_id,
            title,
            message,
            notification_type,
            entity_type,
            entity_id,
            details,
            dedupe_key,
        )


def notify_users_batch(
    db: Session,
    notifications: Iterable[dict[str, object]],
) -> None:
    iterator = iter(notifications)
    while batch := list(islice(iterator, NOTIFICATION_INSERT_BATCH_SIZE)):
        db.execute(
            postgres_insert(Notification)
            .values(batch)
            .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
        )
