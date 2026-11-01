from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
from app.modules.monitoring.application import service
from app.modules.monitoring.application.service import _schedule_payload
from app.modules.monitoring.schemas.monitoring import MonitoringUpdate
from app.shared.models import User

router = APIRouter()
EDITORS = require_roles("administrator", "data_analyst")
ADMIN = require_roles("administrator")


@router.get("")
def get_monitoring_schedule(_: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return service.get_monitoring_schedule(_, db)


@router.patch("")
def update_monitoring_schedule(payload: MonitoringUpdate, user: User = Depends(ADMIN), db: Session = Depends(get_db)) -> dict:
    return service.update_monitoring_schedule(payload, user, db)


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
def request_monitoring_run(user: User = Depends(EDITORS), db: Session = Depends(get_db)) -> dict:
    return service.request_monitoring_run(user, db)


@router.get("/dashboard")
def monitoring_dashboard(
    include_dataset: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return service.monitoring_dashboard(include_dataset, user, db)
