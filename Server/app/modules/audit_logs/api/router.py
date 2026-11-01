from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_roles
from app.modules.audit_logs.application.queries import list_audit_logs as query_audit_logs
from app.shared.models import User

router = APIRouter()
ADMIN = require_roles("administrator")


@router.get("")
def list_audit_logs(
    user_id: int | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(ADMIN),
    db: Session = Depends(get_db),
) -> list[dict]:
    return query_audit_logs(
        db, user_id, action, entity_type, start_date, end_date, limit, offset
    )
