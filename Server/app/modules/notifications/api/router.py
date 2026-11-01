from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
from app.modules.notifications.application.service import (
    archive_notifications as archive_notifications_use_case,
    archive_notifications_dry_run as archive_notifications_dry_run_use_case,
    list_notifications as list_notifications_use_case,
    mark_all_notifications_read as mark_all_notifications_read_use_case,
    mark_notification_read as mark_notification_read_use_case,
    notification_payload,
    restore_notifications as restore_notifications_use_case,
    restore_notifications_dry_run as restore_notifications_dry_run_use_case,
    unread_notification_count as unread_notification_count_use_case,
)
from app.modules.notifications.schemas.notification import NotificationArchiveInput, NotificationRestoreInput
from app.shared.models import User

router = APIRouter()
ADMIN = require_roles("administrator")


@router.get("/unread-count")
def unread_notification_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, int]:
    return unread_notification_count_use_case(db, user)


@router.get("")
def list_notifications(
    notification_type: str | None = None,
    is_read: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return list_notifications_use_case(db, user, notification_type, is_read, limit, offset)


@router.post("/archive/dry-run")
def archive_notifications_dry_run(payload: NotificationArchiveInput, _: User = Depends(ADMIN), db: Session = Depends(get_db)) -> dict[str, int]:
    return archive_notifications_dry_run_use_case(db, payload)


@router.post("/archive")
def archive_notifications(payload: NotificationArchiveInput, user: User = Depends(ADMIN), db: Session = Depends(get_db)) -> dict[str, int]:
    return archive_notifications_use_case(db, user, payload)


@router.post("/restore/dry-run")
def restore_notifications_dry_run(payload: NotificationRestoreInput, user: User = Depends(ADMIN), db: Session = Depends(get_db)) -> dict[str, int]:
    return restore_notifications_dry_run_use_case(db, user, payload)


@router.post("/restore")
def restore_notifications(payload: NotificationRestoreInput, user: User = Depends(ADMIN), db: Session = Depends(get_db)) -> dict[str, int]:
    return restore_notifications_use_case(db, user, payload)


@router.patch("/{notification_id}/read")
def mark_notification_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return mark_notification_read_use_case(db, user, notification_id)


@router.post("/read-all")
def mark_all_notifications_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, int]:
    return mark_all_notifications_read_use_case(db, user)
