from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.modules.dashboard.application import service
from app.shared.models import User

router = APIRouter()


@router.get("")
def dashboard(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    include_dataset: bool = False,
) -> dict:
    return service.dashboard(user, db, include_dataset)
