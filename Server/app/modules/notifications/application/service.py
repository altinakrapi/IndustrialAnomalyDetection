from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.notifications.schemas.notification import NotificationArchiveInput, NotificationRestoreInput
from app.shared.models import AuditLog, Notification, User


def notification_payload(notification: Notification) -> dict:
    return {
        "id": notification.id,
        "title": notification.title,
        "message": notification.message,
        "notification_type": notification.notification_type,
        "details": notification.details,
        "is_read": notification.is_read,
        "related_entity_type": notification.related_entity_type,
        "related_entity_id": notification.related_entity_id,
        "created_at": notification.created_at,
    }


def unread_notification_count(db: Session, user: User) -> dict[str, int]:
    count = db.scalar(select(func.count(Notification.id)).where(
        Notification.user_id == user.id,
        Notification.is_read.is_(False),
        Notification.archived_at.is_(None),
    ))
    return {"unread_count": count or 0}


def list_notifications(db: Session, user: User, notification_type, is_read, limit, offset) -> list[dict]:
    query = select(Notification).where(Notification.user_id == user.id, Notification.archived_at.is_(None))
    if notification_type:
        query = query.where(Notification.notification_type == notification_type)
    if is_read is not None:
        query = query.where(Notification.is_read == is_read)
    query = query.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
    return [notification_payload(item) for item in db.scalars(query)]


def _archive_filter(db: Session, payload: NotificationArchiveInput):
    query = select(Notification.id).where(
        Notification.archived_at.is_(None),
        Notification.created_at < payload.before,
        Notification.notification_type == payload.notification_type,
    )
    if payload.related_entity_type:
        query = query.where(Notification.related_entity_type == payload.related_entity_type)
    if payload.analysis_run_id is not None:
        query = query.where(Notification.details["analysis"]["run_id"].as_integer() == payload.analysis_run_id)
    return query


def _restore_filter(payload: NotificationRestoreInput):
    query = select(Notification.id).where(
        Notification.archived_at.is_not(None),
        Notification.archived_at < payload.archived_before,
        Notification.notification_type == payload.notification_type,
    )
    if payload.related_entity_type:
        query = query.where(Notification.related_entity_type == payload.related_entity_type)
    if payload.analysis_run_id is not None:
        query = query.where(Notification.details["analysis"]["run_id"].as_integer() == payload.analysis_run_id)
    return query


def archive_notifications_dry_run(db: Session, payload: NotificationArchiveInput) -> dict[str, int]:
    count = db.scalar(select(func.count()).select_from(_archive_filter(db, payload).subquery()))
    return {"count": count or 0}


def archive_notifications(db: Session, user: User, payload: NotificationArchiveInput) -> dict[str, int]:
    selected_ids = list(db.scalars(_archive_filter(db, payload)))
    if selected_ids:
        db.execute(update(Notification).where(Notification.id.in_(selected_ids)).values(archived_at=datetime.now(payload.before.tzinfo)))
    db.add(AuditLog(user_id=user.id, action="archive", entity_type="notification", entity_id=None, details={
        "count": len(selected_ids), "before": payload.before.isoformat(), "notification_type": payload.notification_type,
        "related_entity_type": payload.related_entity_type, "analysis_run_id": payload.analysis_run_id,
    }))
    db.commit()
    return {"archived": len(selected_ids)}


def restore_notifications_dry_run(db: Session, user: User, payload: NotificationRestoreInput) -> dict[str, int]:
    count = db.scalar(select(func.count()).select_from(_restore_filter(payload).subquery()))
    db.add(AuditLog(user_id=user.id, action="restore_notifications_dry_run", entity_type="notification", entity_id=None,
                details={"count": count or 0, "filter": payload.model_dump(mode="json")}))
    db.commit()
    return {"count": count or 0}


def restore_notifications(db: Session, user: User, payload: NotificationRestoreInput) -> dict[str, int]:
    selected_ids = list(db.scalars(_restore_filter(payload)))
    if selected_ids:
        db.execute(update(Notification).where(Notification.id.in_(selected_ids)).values(archived_at=None))
    db.add(AuditLog(user_id=user.id, action="restore_notifications", entity_type="notification", entity_id=None,
                    details={"count": len(selected_ids), "filter": payload.model_dump(mode="json")}))
    db.commit()
    return {"restored": len(selected_ids)}


def mark_notification_read(db: Session, user: User, notification_id: int) -> dict:
    notification = db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id, Notification.archived_at.is_(None)))
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.is_read = True
    db.commit()
    db.refresh(notification)
    return notification_payload(notification)


def mark_all_notifications_read(db: Session, user: User) -> dict[str, int]:
    notifications = db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.is_read.is_(False), Notification.archived_at.is_(None)))
    count = 0
    for notification in notifications:
        notification.is_read = True
        count += 1
    db.commit()
    return {"updated": count}
