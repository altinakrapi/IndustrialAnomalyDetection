from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.models import AuditLog


def list_audit_logs(
    db: Session,
    user_id: int | None,
    action: str | None,
    entity_type: str | None,
    start_date,
    end_date,
    limit: int,
    offset: int,
) -> list[dict]:
    query = select(AuditLog)
    if user_id is not None:
        query = query.where(AuditLog.user_id == user_id)
    if action:
        query = query.where(AuditLog.action == action)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if start_date:
        query = query.where(AuditLog.created_at >= start_date)
    if end_date:
        query = query.where(AuditLog.created_at <= end_date)
    query = query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    return [
        {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "details": log.details,
            "ip_address": log.ip_address,
            "created_at": log.created_at,
        }
        for log in db.scalars(query)
    ]
