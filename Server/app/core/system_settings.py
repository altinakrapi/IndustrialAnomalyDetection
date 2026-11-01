from sqlalchemy.orm import Session

from app.core.config import settings
from app.shared.models import SystemSetting


def get_system_setting(db: Session, key: str, default):
    setting = db.get(SystemSetting, key)
    return setting.value if setting is not None else default
