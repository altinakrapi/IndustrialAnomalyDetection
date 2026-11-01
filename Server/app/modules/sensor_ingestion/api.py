from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_roles
from app.modules.sensor_ingestion import application as service
from app.modules.sensor_ingestion.schemas import (
    BatchReadingInput,
    SensorReadingBatchInput,
    SensorReadingInput,
)
from app.shared.models import DeviceAPIKey, User

router = APIRouter()
key_router = APIRouter()
ADMINISTRATORS = require_roles("administrator")

_check_rate_limit = service._check_rate_limit

_new_device_key = service._new_device_key
_prepare_reading = service._prepare_reading
_authorized_device = service._authorized_device
_validate_sensor_timestamp = service._validate_sensor_timestamp
_measurement_payload = service._measurement_payload
record_equipment_measurement = service.record_equipment_measurement
MAX_BATCH_SIZE = service.MAX_BATCH_SIZE
MAX_READINGS_PER_MINUTE = service.MAX_READINGS_PER_MINUTE

_service_ingest_one = service._ingest_one


def _ingest_one(*args, **kwargs):
    service.record_equipment_measurement = record_equipment_measurement
    service._measurement_payload = _measurement_payload
    return _service_ingest_one(*args, **kwargs)


def authenticate_device_api_key(
    x_device_api_key: str | None = Header(default=None, alias="X-Device-API-Key"),
    db: Session = Depends(get_db),
) -> DeviceAPIKey:
    return service.authenticate_device_api_key(x_device_api_key, db)


@key_router.post("/devices/{device_id}", status_code=status.HTTP_201_CREATED)
def create_device_api_key(
    device_id: int,
    administrator: User = Depends(ADMINISTRATORS),
    db: Session = Depends(get_db),
) -> dict:
    return service.create_device_api_key(device_id, administrator, db)


@key_router.post("/devices/{device_id}/rotate")
def rotate_device_api_key(
    device_id: int,
    administrator: User = Depends(ADMINISTRATORS),
    db: Session = Depends(get_db),
) -> dict:
    return service.rotate_device_api_key(device_id, administrator, db)


@key_router.get("/devices/{device_id}")
def list_device_api_keys(
    device_id: int,
    _: User = Depends(ADMINISTRATORS),
    db: Session = Depends(get_db),
) -> list[dict]:
    return service.list_device_api_keys(device_id, _, db)


@key_router.delete("/devices/{device_id}/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_device_api_key(
    device_id: int,
    key_id: int,
    administrator: User = Depends(ADMINISTRATORS),
    db: Session = Depends(get_db),
) -> None:
    return service.revoke_device_api_key(device_id, key_id, administrator, db)


@router.post("", status_code=status.HTTP_201_CREATED)
def ingest_reading(
    payload: SensorReadingInput,
    api_key: DeviceAPIKey = Depends(authenticate_device_api_key),
    db: Session = Depends(get_db),
) -> dict:
    service._authorized_device = _authorized_device
    service._ingest_one = _ingest_one
    service._prepare_reading = _prepare_reading
    service._measurement_payload = _measurement_payload
    service.record_equipment_measurement = record_equipment_measurement
    return service.ingest_reading(payload, api_key, db)


@router.post("/batch")
def ingest_reading_batch(
    payload: SensorReadingBatchInput,
    api_key: DeviceAPIKey = Depends(authenticate_device_api_key),
    db: Session = Depends(get_db),
) -> dict:
    service._authorized_device = _authorized_device
    service._ingest_one = _ingest_one
    service._prepare_reading = _prepare_reading
    service._measurement_payload = _measurement_payload
    service.record_equipment_measurement = record_equipment_measurement
    return service.ingest_reading_batch(payload, api_key, db)
