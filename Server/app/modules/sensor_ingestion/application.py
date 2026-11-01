import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_roles
from app.modules.devices.application.plausibility import physical_measurement_issue
from app.modules.devices.api.equipment import _measurement_payload
from app.modules.devices.application.measurement_recording import (
    record_equipment_measurement,
)
from app.shared.models import (
    AuditLog,
    Device,
    DeviceAPIKey,
    EquipmentParameter,
    User,
)

ADMINISTRATORS = require_roles("administrator")
MAX_BATCH_SIZE = 100
MAX_READINGS_PER_MINUTE = 120
MAX_SENSOR_AGE = timedelta(days=30)
MAX_SENSOR_FUTURE_SKEW = timedelta(minutes=5)


class SensorReadingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_identifier: str = Field(
        min_length=1,
        max_length=100,
        description="Exact registered device code associated with the API key.",
    )
    parameter: str = Field(
        min_length=1,
        max_length=100,
        description="Enabled equipment parameter name, matched case-insensitively.",
    )
    value: float = Field(description="Finite measurement in the parameter's unit.")
    unit: str = Field(min_length=1, max_length=30)
    sensor_timestamp: datetime | None = Field(
        default=None,
        description="Optional timezone-aware sensor time; limited to 30 days old and 5 minutes ahead.",
    )
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=60)

    @field_validator("device_identifier", "parameter", "unit")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("This field cannot be blank")
        return normalized

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: float) -> float:
        if not float("-inf") < value < float("inf"):
            raise ValueError("Sensor reading value must be finite")
        return value

    @field_validator("sensor_timestamp")
    @classmethod
    def require_timezone_aware_timestamp(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("sensor_timestamp must include a timezone")
        return value


class BatchReadingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=30)
    sensor_timestamp: datetime | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=60)

    @field_validator("parameter", "unit")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("This field cannot be blank")
        return normalized

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: float) -> float:
        if not float("-inf") < value < float("inf"):
            raise ValueError("Sensor reading value must be finite")
        return value

    @field_validator("sensor_timestamp")
    @classmethod
    def require_timezone_aware_timestamp(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("sensor_timestamp must include a timezone")
        return value


class SensorReadingBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_identifier: str = Field(min_length=1, max_length=100)
    readings: list[BatchReadingInput] = Field(
        min_length=1, max_length=MAX_BATCH_SIZE
    )

    @field_validator("device_identifier")
    @classmethod
    def normalize_device_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("device_identifier cannot be blank")
        return normalized


def _key_hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _key_metadata(api_key: DeviceAPIKey) -> dict:
    return {
        "id": api_key.id,
        "device_id": api_key.device_id,
        "key_prefix": api_key.key_prefix,
        "created_at": api_key.created_at,
        "revoked_at": api_key.revoked_at,
        "last_used_at": api_key.last_used_at,
        "created_by": api_key.created_by,
    }


def _new_device_key(
    db: Session, device: Device, administrator: User
) -> tuple[DeviceAPIKey, str]:
    token = f"iad_{secrets.token_urlsafe(32)}"
    api_key = DeviceAPIKey(
        device_id=device.id,
        key_hash=_key_hash(token),
        key_prefix=token[:12],
        created_by=administrator.id,
    )
    db.add(api_key)
    db.flush()
    db.add(
        AuditLog(
            user_id=administrator.id,
            action="create",
            entity_type="device_api_key",
            entity_id=api_key.id,
            details={"device_id": device.id, "key_prefix": api_key.key_prefix},
        )
    )
    return api_key, token


def create_device_api_key(
    device_id: int,
    administrator: User = Depends(ADMINISTRATORS),
    db: Session = Depends(get_db),
) -> dict:
    device = db.get(Device, device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Registered device not found")
    active_key = db.scalar(
        select(DeviceAPIKey.id).where(
            DeviceAPIKey.device_id == device.id,
            DeviceAPIKey.revoked_at.is_(None),
        )
    )
    if active_key is not None:
        raise HTTPException(
            status_code=409,
            detail="This device already has an active API key; use the rotate endpoint.",
        )
    try:
        api_key, token = _new_device_key(db, device, administrator)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This device already has an active API key; use the rotate endpoint.",
        ) from error
    db.refresh(api_key)
    return {**_key_metadata(api_key), "api_key": token}


def rotate_device_api_key(
    device_id: int,
    administrator: User = Depends(ADMINISTRATORS),
    db: Session = Depends(get_db),
) -> dict:
    device = db.get(Device, device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Registered device not found")
    now = datetime.now(timezone.utc)
    active_keys = list(
        db.scalars(
            select(DeviceAPIKey)
            .where(
                DeviceAPIKey.device_id == device.id,
                DeviceAPIKey.revoked_at.is_(None),
            )
            .with_for_update()
        )
    )
    for api_key in active_keys:
        api_key.revoked_at = now
        db.add(
            AuditLog(
                user_id=administrator.id,
                action="revoke",
                entity_type="device_api_key",
                entity_id=api_key.id,
                details={"device_id": device.id, "reason": "rotation"},
            )
        )
    try:
        api_key, token = _new_device_key(db, device, administrator)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Another key rotation completed concurrently; retry the request.",
        ) from error
    db.refresh(api_key)
    return {**_key_metadata(api_key), "api_key": token}


def list_device_api_keys(
    device_id: int,
    _: User = Depends(ADMINISTRATORS),
    db: Session = Depends(get_db),
) -> list[dict]:
    device = db.get(Device, device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Registered device not found")
    return [
        _key_metadata(api_key)
        for api_key in db.scalars(
            select(DeviceAPIKey)
            .where(DeviceAPIKey.device_id == device.id)
            .order_by(DeviceAPIKey.created_at.desc())
        )
    ]


def revoke_device_api_key(
    device_id: int,
    key_id: int,
    administrator: User = Depends(ADMINISTRATORS),
    db: Session = Depends(get_db),
) -> None:
    api_key = db.scalar(
        select(DeviceAPIKey).where(
            DeviceAPIKey.id == key_id,
            DeviceAPIKey.device_id == device_id,
        )
    )
    if api_key is None:
        raise HTTPException(status_code=404, detail="Device API key not found")
    if api_key.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Device API key is already revoked")
    api_key.revoked_at = datetime.now(timezone.utc)
    db.add(
        AuditLog(
            user_id=administrator.id,
            action="revoke",
            entity_type="device_api_key",
            entity_id=api_key.id,
            details={"device_id": device_id},
        )
    )
    db.commit()


def authenticate_device_api_key(
    x_device_api_key: str | None = Header(default=None, alias="X-Device-API-Key"),
    db: Session = Depends(get_db),
) -> DeviceAPIKey:
    if not x_device_api_key:
        raise HTTPException(
            status_code=401,
            detail="A device API key is required",
            headers={"WWW-Authenticate": "DeviceApiKey"},
        )
    api_key = db.scalar(
        select(DeviceAPIKey)
        .where(
            DeviceAPIKey.key_hash == _key_hash(x_device_api_key),
            DeviceAPIKey.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if api_key is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked device API key",
            headers={"WWW-Authenticate": "DeviceApiKey"},
        )
    return api_key


def _validate_sensor_timestamp(sensor_timestamp: datetime | None, now: datetime) -> None:
    if sensor_timestamp is None:
        return
    normalized = sensor_timestamp.astimezone(timezone.utc)
    if normalized > now + MAX_SENSOR_FUTURE_SKEW:
        raise HTTPException(
            status_code=422,
            detail="sensor_timestamp cannot be more than five minutes in the future",
        )
    if normalized < now - MAX_SENSOR_AGE:
        raise HTTPException(
            status_code=422,
            detail="sensor_timestamp is too old; readings older than 30 days are rejected",
        )


def _authorized_device(
    device_identifier: str, api_key: DeviceAPIKey, db: Session
) -> Device:
    device = db.scalar(
        select(Device).where(
            Device.id == api_key.device_id,
            Device.device_code == device_identifier,
            Device.is_registered.is_(True),
            Device.status == "active",
        )
    )
    if device is None:
        raise HTTPException(
            status_code=404,
            detail="Active registered device does not match this API key",
        )
    return device


def _check_rate_limit(
    db: Session, api_key: DeviceAPIKey, now: datetime, incoming_count: int
) -> None:
    locked_key = db.scalar(
        select(DeviceAPIKey)
        .where(
            DeviceAPIKey.id == api_key.id,
            DeviceAPIKey.revoked_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_key is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked device API key")
    if (
        locked_key.rate_window_started_at is None
        or now - locked_key.rate_window_started_at >= timedelta(minutes=1)
    ):
        locked_key.rate_window_started_at = now
        locked_key.rate_count = 0
    if locked_key.rate_count + incoming_count > MAX_READINGS_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail="Sensor ingestion limit exceeded; maximum is 120 readings per device per minute",
            headers={"Retry-After": "60"},
        )
    locked_key.rate_count += incoming_count
    db.commit()
    db.refresh(api_key)


def _prepare_reading(
    reading: SensorReadingInput | BatchReadingInput,
    device: Device,
    db: Session,
) -> EquipmentParameter:
    parameter = db.scalar(
        select(EquipmentParameter).where(
            EquipmentParameter.device_id == device.id,
            func.lower(EquipmentParameter.name) == reading.parameter.casefold(),
            EquipmentParameter.enabled.is_(True),
        )
    )
    if parameter is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown or disabled parameter: {reading.parameter}",
        )
    if parameter.unit != reading.unit:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unit mismatch for {parameter.name}: expected {parameter.unit}, "
                f"received {reading.unit}"
            ),
        )
    physical_issue = physical_measurement_issue(
        parameter.name, parameter.unit, reading.value
    )
    if physical_issue is not None:
        raise HTTPException(status_code=422, detail=physical_issue)
    return parameter


def _ingest_one(
    reading: SensorReadingInput | BatchReadingInput,
    device: Device,
    parameter: EquipmentParameter,
    api_key: DeviceAPIKey,
    db: Session,
    received_at: datetime,
) -> dict:
    _validate_sensor_timestamp(reading.sensor_timestamp, received_at)
    measurement_time = (
        reading.sensor_timestamp.astimezone(timezone.utc)
        if reading.sensor_timestamp is not None
        else received_at
    )
    idempotency_key = (
        f"sensor:{api_key.id}:{reading.idempotency_key}"
        if reading.idempotency_key
        else None
    )
    try:
        measurement, run, duplicate = record_equipment_measurement(
            db,
            device,
            parameter,
            reading.value,
            None,
            source_type=(
                "simulated" if device.source_type == "simulated" else "sensor"
            ),
            source="sensor",
            measured_at=measurement_time,
            received_at=received_at,
            sensor_timestamp=reading.sensor_timestamp,
            sensor_api_key_id=api_key.id,
            idempotency_key=idempotency_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        **_measurement_payload(measurement),
        "analysis_run_id": run.id if run else None,
        "anomaly_count": run.anomalies_found if run else 0,
        "analysis_status": run.status if run else "not_started",
        "alert_evaluation": (
            (run.metrics or {}).get("alert_evaluation") if run else None
        ),
        "duplicate": duplicate,
    }


def ingest_reading(
    payload: SensorReadingInput,
    api_key: DeviceAPIKey = Depends(authenticate_device_api_key),
    db: Session = Depends(get_db),
) -> dict:
    received_at = datetime.now(timezone.utc)
    device = _authorized_device(payload.device_identifier, api_key, db)
    parameter = _prepare_reading(payload, device, db)
    _validate_sensor_timestamp(payload.sensor_timestamp, received_at)
    _check_rate_limit(db, api_key, received_at, 1)
    api_key.last_used_at = received_at
    result = _ingest_one(payload, device, parameter, api_key, db, received_at)
    return result


def ingest_reading_batch(
    payload: SensorReadingBatchInput,
    api_key: DeviceAPIKey = Depends(authenticate_device_api_key),
    db: Session = Depends(get_db),
) -> dict:
    received_at = datetime.now(timezone.utc)
    device = _authorized_device(payload.device_identifier, api_key, db)
    prepared = [
        (reading, _prepare_reading(reading, device, db))
        for reading in payload.readings
    ]
    seen_timestamps: set[tuple[int, datetime]] = set()
    seen_idempotency_keys: set[str] = set()
    for reading, parameter in prepared:
        _validate_sensor_timestamp(reading.sensor_timestamp, received_at)
        timestamp_key = (
            parameter.id,
            (
                reading.sensor_timestamp.astimezone(timezone.utc)
                if reading.sensor_timestamp is not None
                else received_at
            ),
        )
        if timestamp_key in seen_timestamps:
            raise HTTPException(
                status_code=422,
                detail="A batch cannot contain duplicate parameter timestamps",
            )
        seen_timestamps.add(timestamp_key)
        if (
            reading.idempotency_key
            and reading.idempotency_key in seen_idempotency_keys
        ):
            raise HTTPException(
                status_code=422,
                detail="A batch cannot reuse an idempotency key",
            )
        if reading.idempotency_key:
            seen_idempotency_keys.add(reading.idempotency_key)
    _check_rate_limit(db, api_key, received_at, len(prepared))
    api_key.last_used_at = received_at
    results = [
        _ingest_one(reading, device, parameter, api_key, db, received_at)
        for reading, parameter in prepared
    ]
    return {
        "device_id": device.id,
        "received_at": received_at,
        "count": len(results),
        "readings": results,
    }
