from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
from app.modules.maintenance.application import maintenance_service
from app.modules.maintenance.schemas.maintenance import ActionInput, MaintenanceOrderInput, MaintenanceUpdate
from app.shared.models import MaintenanceOrder, User

router = APIRouter()
MAINTENANCE_ROLES = require_roles("administrator", "data_analyst")
TECHNICIANS = require_roles("administrator", "maintenance_technician")
ORDER_STATUSES = maintenance_service.ORDER_STATUSES
PRIORITIES = maintenance_service.PRIORITIES


def notify_approaching_deadlines(db: Session) -> None:
    return maintenance_service.notify_approaching_deadlines(db)


def order_payload(order: MaintenanceOrder) -> dict:
    return maintenance_service.order_payload(order)


@router.get("")
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
    return maintenance_service.list_orders(status_filter, device_id, assigned_to, priority, overdue, limit, offset, user, db)


@router.get("/technicians")
def list_technicians(_: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> list[dict]:
    return maintenance_service.list_technicians(_, db)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_order(payload: MaintenanceOrderInput, user: User = Depends(MAINTENANCE_ROLES), db: Session = Depends(get_db)) -> dict:
    return maintenance_service.create_order(payload, user, db)


@router.patch("/{order_id}")
def update_order(order_id: int, payload: MaintenanceUpdate, user: User = Depends(TECHNICIANS), db: Session = Depends(get_db)) -> dict:
    return maintenance_service.update_order(order_id, payload, user, db)


@router.post("/{order_id}/actions", status_code=status.HTTP_201_CREATED)
def create_action(order_id: int, payload: ActionInput, user: User = Depends(TECHNICIANS), db: Session = Depends(get_db)) -> dict:
    return maintenance_service.create_action(order_id, payload, user, db)
