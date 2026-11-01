from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
from app.core.notifications import notify_user
from app.shared.models import (
    AuditLog,
    Device,
    MaintenanceAction,
    MaintenanceOrder,
    Notification,
    User,
)

router = APIRouter()
MAINTENANCE_ROLES = require_roles("administrator", "data_analyst")
TECHNICIANS = require_roles("administrator", "maintenance_technician")
ORDER_STATUSES = {"new", "assigned", "in_progress", "on_hold", "completed"}
PRIORITIES = {"low", "medium", "high", "critical"}


def notify_approaching_deadlines(db: Session) -> None:
    now = datetime.now(timezone.utc)
    orders = db.scalars(
        select(MaintenanceOrder).where(
            MaintenanceOrder.status != "completed",
            MaintenanceOrder.assigned_to.is_not(None),
            MaintenanceOrder.due_date <= now + timedelta(hours=24),
        )
    )
    for order in orders:
        recent = db.scalar(
            select(Notification.id).where(
                Notification.user_id == order.assigned_to,
                Notification.notification_type == "maintenance_due",
                Notification.related_entity_type == "maintenance_order",
                Notification.related_entity_id == order.id,
                Notification.created_at >= now - timedelta(hours=24),
            )
        )
        if recent is None:
            notify_user(
                db,
                order.assigned_to,
                "Maintenance deadline approaching",
                f"Order #{order.id} is due {order.due_date.isoformat()}.",
                "maintenance_due",
                "maintenance_order",
                order.id,
            )


class MaintenanceOrderInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    device_id: int | None = None
    assigned_to: int | None = None
    priority: str = "medium"
    due_date: datetime | None = None


class MaintenanceUpdate(BaseModel):
    status: str | None = None
    assigned_to: int | None = None
    priority: str | None = None
    due_date: datetime | None = None
    resolution_notes: str | None = None


class ActionInput(BaseModel):
    action_description: str = Field(min_length=1)
    findings: str | None = None
    recommendations: str | None = None
    issue_confirmed: bool | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


def order_payload(order: MaintenanceOrder) -> dict:
    device = order.device if order.device and order.device.is_registered else None
    dataset = (
        order.anomaly.analysis_run.dataset
        if order.anomaly and order.anomaly.analysis_run
        else None
    )
    return {
        "id": order.id,
        "title": order.title,
        "description": order.description,
        "device_id": order.device_id,
        "device_name": device.name if device else None,
        "device_code": device.device_code if device else None,
        "dataset_id": dataset.id if dataset else None,
        "dataset_name": dataset.name if dataset else None,
        "anomaly_id": order.anomaly_id,
        "assigned_to": order.assigned_to,
        "assigned_name": order.assignee.full_name if hasattr(order, "assignee") and order.assignee else None,
        "priority": order.priority,
        "status": order.status,
        "resolution_notes": order.resolution_notes,
        "due_date": order.due_date,
        "completed_at": order.completed_at,
        "created_at": order.created_at,
        "actions": [
            {
                "id": action.id,
                "action_description": action.action_description,
                "findings": action.findings,
                "recommendations": action.recommendations,
                "issue_confirmed": action.issue_confirmed,
                "started_at": action.started_at,
                "completed_at": action.completed_at,
                "created_at": action.created_at,
            }
            for action in order.actions
        ],
    }


def list_orders(
    status_filter: str | None = None,
    device_id: int | None = None,
    assigned_to: int | None = None,
    priority: str | None = None,
    overdue: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    notify_approaching_deadlines(db)
    db.commit()
    query = select(MaintenanceOrder).order_by(MaintenanceOrder.created_at.desc())
    if user.role == "maintenance_technician":
        query = query.where(MaintenanceOrder.assigned_to == user.id)
    if status_filter:
        query = query.where(MaintenanceOrder.status == status_filter)
    if device_id:
        query = query.where(MaintenanceOrder.device_id == device_id)
    if assigned_to:
        query = query.where(MaintenanceOrder.assigned_to == assigned_to)
    if priority:
        query = query.where(MaintenanceOrder.priority == priority)
    if overdue is True:
        query = query.where(
            MaintenanceOrder.due_date < datetime.now(timezone.utc),
            MaintenanceOrder.status != "completed",
        )
    elif overdue is False:
        query = query.where(
            (MaintenanceOrder.due_date.is_(None))
            | (MaintenanceOrder.due_date >= datetime.now(timezone.utc))
            | (MaintenanceOrder.status == "completed")
        )
    query = query.limit(limit).offset(offset)
    return [order_payload(order) for order in db.scalars(query)]


def list_technicians(
    _: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)
) -> list[dict]:
    return [
        {
            "id": account.id,
            "full_name": account.full_name,
            "email": account.email,
            "role": account.role,
        }
        for account in db.scalars(
            select(User)
            .where(
                User.is_active.is_(True),
                User.role.in_({"administrator", "maintenance_technician"}),
            )
            .order_by(User.full_name)
        )
    ]


def create_order(
    payload: MaintenanceOrderInput,
    user: User = Depends(MAINTENANCE_ROLES),
    db: Session = Depends(get_db),
) -> dict:
    if not payload.device_id:
        raise HTTPException(status_code=422, detail="device_id is required")
    if payload.priority not in PRIORITIES:
        raise HTTPException(status_code=422, detail="Unsupported priority")
    if payload.assigned_to is not None and user.role != "administrator":
        raise HTTPException(
            status_code=403,
            detail="Only administrators may assign maintenance orders",
        )
    device = db.get(Device, payload.device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Device not found")
    if payload.assigned_to:
        assignee = db.get(User, payload.assigned_to)
        if assignee is None or not assignee.is_active or assignee.role not in {
            "administrator",
            "maintenance_technician",
        }:
            raise HTTPException(status_code=422, detail="Assignee must be an active technician")
    order = MaintenanceOrder(
        title=payload.title.strip(),
        description=payload.description,
        device_id=payload.device_id,
        assigned_to=payload.assigned_to,
        created_by=user.id,
        priority=payload.priority,
        status="assigned" if payload.assigned_to else "new",
        due_date=payload.due_date,
    )
    db.add(order)
    db.flush()
    db.add(AuditLog(user_id=user.id, action="create", entity_type="maintenance_order", entity_id=order.id))
    notify_user(
        db,
        order.assigned_to,
        "Maintenance order assigned",
        f"Order #{order.id}: {order.title}",
        "maintenance_assigned",
        "maintenance_order",
        order.id,
    )
    db.commit()
    db.refresh(order)
    return order_payload(order)


def update_order(
    order_id: int,
    payload: MaintenanceUpdate,
    user: User = Depends(TECHNICIANS),
    db: Session = Depends(get_db),
) -> dict:
    order = db.get(MaintenanceOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Maintenance order not found")
    updates = payload.model_dump(exclude_unset=True)
    previous_assignee = order.assigned_to
    previous_status = order.status
    if "assigned_to" in updates and user.role != "administrator":
        raise HTTPException(
            status_code=403,
            detail="Only administrators may assign or reassign maintenance orders",
        )
    if user.role != "administrator" and order.assigned_to != user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the assigned technician may update this maintenance order",
        )
    if updates.get("status") is not None and updates["status"] not in ORDER_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported maintenance status")
    if updates.get("priority") is not None and updates["priority"] not in PRIORITIES:
        raise HTTPException(status_code=422, detail="Unsupported priority")
    if "assigned_to" in updates and updates["assigned_to"] is not None:
        assignee = db.get(User, updates["assigned_to"])
        if assignee is None or not assignee.is_active or assignee.role not in {
            "administrator",
            "maintenance_technician",
        }:
            raise HTTPException(status_code=422, detail="Assignee must be an active technician")
    if updates.get("status") == "completed":
        if not updates.get("resolution_notes", order.resolution_notes) and not order.actions:
            raise HTTPException(
                status_code=409,
                detail="Record resolution notes or a maintenance action before closing this order",
            )
        order.completed_at = datetime.now(timezone.utc)
    for key, value in updates.items():
        setattr(order, key, value)
    if "assigned_to" in updates and updates["assigned_to"] != previous_assignee:
        notify_user(
            db,
            previous_assignee,
            "Maintenance order reassigned",
            f"Order #{order.id} is no longer assigned to you.",
            "maintenance_reassigned",
            "maintenance_order",
            order.id,
        )
        notify_user(
            db,
            updates["assigned_to"],
            "Maintenance order assigned",
            f"Order #{order.id}: {order.title}",
            "maintenance_assigned",
            "maintenance_order",
            order.id,
        )
    if any(key in updates for key in ("priority", "due_date")):
        notify_user(
            db,
            order.assigned_to,
            "Maintenance order updated",
            f"Priority or due date changed for order #{order.id}: {order.title}.",
            "maintenance_updated",
            "maintenance_order",
            order.id,
        )
    if "status" in updates and updates["status"] != previous_status:
        notify_user(
            db,
            order.created_by,
            "Maintenance order status updated",
            f"Order #{order.id} is now {updates['status'].replace('_', ' ')}.",
            "maintenance_status",
            "maintenance_order",
            order.id,
        )
    db.add(AuditLog(user_id=user.id, action="update", entity_type="maintenance_order", entity_id=order.id, details=updates))
    db.commit()
    db.refresh(order)
    return order_payload(order)


def create_action(
    order_id: int,
    payload: ActionInput,
    user: User = Depends(TECHNICIANS),
    db: Session = Depends(get_db),
) -> dict:
    order = db.get(MaintenanceOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Maintenance order not found")
    if user.role != "administrator" and order.assigned_to != user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the assigned technician may record inspection findings",
        )
    if order.status == "completed":
        raise HTTPException(status_code=409, detail="Completed maintenance orders cannot be changed")
    action = MaintenanceAction(
        maintenance_order_id=order.id,
        performed_by=user.id,
        **payload.model_dump(),
    )
    if order.status in {"new", "assigned"}:
        order.status = "in_progress"
    db.add(action)
    db.add(AuditLog(user_id=user.id, action="record_action", entity_type="maintenance_order", entity_id=order.id))
    if order.created_by != user.id:
        notify_user(
            db,
            order.created_by,
            "Maintenance work updated",
            f"New work was recorded for order #{order.id}: {order.title}.",
            "maintenance_action",
            "maintenance_order",
            order.id,
        )
    db.commit()
    db.refresh(action)
    return {
        "id": action.id,
        "action_description": action.action_description,
        "findings": action.findings,
        "recommendations": action.recommendations,
        "issue_confirmed": action.issue_confirmed,
        "created_at": action.created_at,
    }