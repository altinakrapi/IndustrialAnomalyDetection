from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
from app.modules.reports.application import report_service
from app.shared.models import User

router = APIRouter()
ANALYSTS = require_roles("administrator", "data_analyst")
REPORT_USERS = require_roles("administrator", "data_analyst", "maintenance_technician")


@router.get("/analysis/{analysis_id}.pdf")
def analysis_report(
    analysis_id: int,
    include_dataset: bool = Query(default=False),
    user: User = Depends(ANALYSTS),
    db: Session = Depends(get_db),
) -> Response:
    return report_service.analysis_report(analysis_id, include_dataset, user, db)


@router.get("/maintenance.csv")
def maintenance_export(
    device_id: int | None = None,
    assigned_to: int | None = None,
    status_filter: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    user: User = Depends(REPORT_USERS),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    return report_service.maintenance_export(device_id, assigned_to, status_filter, start_date, end_date, user, db)


@router.get("/device/{device_id}.pdf")
def device_report(
    device_id: int,
    user: User = Depends(REPORT_USERS),
    db: Session = Depends(get_db),
) -> Response:
    return report_service.device_report(device_id, user, db)


@router.get("/history")
def report_history(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return report_service.report_history(limit, offset, user, db)
