from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_roles
from app.modules.settings.application.service import (
    get_settings as get_settings_use_case,
    settings_payload,
    update_settings as update_settings_use_case,
)
from app.modules.settings.schemas.settings import SettingsUpdate
from app.shared.models import User

SettingsUpdate = SettingsUpdate

router = APIRouter()
ADMIN = require_roles("administrator")


@router.get("")
def get_settings(
    _: User = Depends(ADMIN), db: Session = Depends(get_db)
) -> dict:
    return get_settings_use_case(db)


@router.patch("")
def update_settings(
    payload: SettingsUpdate,
    user: User = Depends(ADMIN),
    db: Session = Depends(get_db),
) -> dict:
    return update_settings_use_case(db, user, payload.model_dump(exclude_unset=True))
