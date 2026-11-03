from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings as environment_settings
from app.core.system_settings import get_system_setting
from app.shared.models import AuditLog, EquipmentParameter, SystemSetting, User


def settings_payload(db: Session) -> dict:
    values = {
        "max_upload_size_mb": get_system_setting(
            db, "max_upload_size_mb", environment_settings.MAX_UPLOAD_SIZE_MB
        ),
        "default_n_estimators": 100,
        "default_contamination": None,
        "default_consecutive_anomaly_threshold": 3,
        "default_persistence_window_size": 5,
        "default_persistence_anomaly_threshold": 3,
        "alert_episode_close_normal_count": 3,
        "alert_escalation_minutes": 60,
        "manual_extreme_z_score_threshold": 10.0,
        "stale_data_interval_multiplier": 3,
        "notification_device_hourly_limit": 20,
        "sensor_ingestion_limit_per_minute": environment_settings.SENSOR_INGESTION_LIMIT_PER_MINUTE,
        "sensor_ingestion_global_request_limit_per_minute": environment_settings.SENSOR_INGESTION_GLOBAL_REQUEST_LIMIT_PER_MINUTE,
    }
    for item in db.scalars(select(SystemSetting)):
        values[item.key] = item.value
    return values


def get_settings(db: Session) -> dict:
    return settings_payload(db)


def update_settings(db: Session, user: User, updates: dict) -> dict:
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one setting to update")
    effective = settings_payload(db)
    for key, value in updates.items():
        if value is not None:
            effective[key] = value
    if effective["default_persistence_anomaly_threshold"] > effective["default_persistence_window_size"]:
        raise HTTPException(status_code=422, detail="The persistence anomaly threshold cannot exceed its window size")
    for window_size, persistence_threshold in db.execute(
        select(
            EquipmentParameter.persistence_window_size,
            EquipmentParameter.persistence_anomaly_threshold,
        )
    ):
        effective_window = window_size or effective["default_persistence_window_size"]
        effective_threshold = persistence_threshold or effective["default_persistence_anomaly_threshold"]
        if effective_threshold > effective_window:
            raise HTTPException(status_code=422, detail="The settings would make at least one equipment parameter's persistence threshold exceed its window size")
    for key, value in updates.items():
        if value is None:
            item = db.get(SystemSetting, key)
            if item is not None:
                db.delete(item)
            continue
        item = db.get(SystemSetting, key)
        if item is None:
            item = SystemSetting(key=key, value=value, updated_by=user.id)
            db.add(item)
        else:
            item.value = value
            item.updated_by = user.id
    db.add(AuditLog(user_id=user.id, action="update", entity_type="system_settings", details=updates))
    db.commit()
    return settings_payload(db)
