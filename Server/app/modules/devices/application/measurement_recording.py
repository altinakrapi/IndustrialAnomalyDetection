import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.notifications import notify_roles, notify_user
from app.modules.devices.application.analysis import analyze_equipment_measurement
from app.shared.models import (
    AnalysisRun,
    AuditLog,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
)

logger = logging.getLogger(__name__)


def _existing_measurement(
    db: Session,
    parameter_id: int,
    value: float,
    source_type: str,
    measured_at: datetime,
    idempotency_key: str | None,
    require_same_timestamp: bool,
) -> tuple[EquipmentMeasurement | None, AnalysisRun | None]:
    existing = None
    if idempotency_key:
        existing = db.scalar(
            select(EquipmentMeasurement).where(
                EquipmentMeasurement.parameter_id == parameter_id,
                EquipmentMeasurement.idempotency_key == idempotency_key,
            )
        )
    if existing is None:
        existing = db.scalar(
            select(EquipmentMeasurement).where(
                EquipmentMeasurement.parameter_id == parameter_id,
                EquipmentMeasurement.measured_at == measured_at,
            )
        )
    if existing is None:
        return None, None
    if (
        existing.value != value
        or existing.source_type != source_type
        or (require_same_timestamp and existing.measured_at != measured_at)
    ):
        raise ValueError(
            "A measurement already exists for this parameter and timestamp "
            "with different data, or the idempotency key was already used."
        )
    run = db.scalar(
        select(AnalysisRun).where(
            AnalysisRun.source_type == "equipment",
            AnalysisRun.execution_key.like(f"equipment:{existing.id}:%"),
        )
    )
    return existing, run


def record_equipment_measurement(
    db: Session,
    device: Device,
    parameter: EquipmentParameter,
    value: float,
    recorded_by: int | None,
    *,
    source_type: str,
    source: str,
    measured_at: datetime,
    received_at: datetime,
    sensor_timestamp: datetime | None = None,
    sensor_api_key_id: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[EquipmentMeasurement, AnalysisRun | None, bool]:
    existing, existing_run = _existing_measurement(
        db,
        parameter.id,
        value,
        source_type,
        measured_at,
        idempotency_key,
        sensor_timestamp is not None,
    )
    if existing is not None:
        return existing, existing_run, True

    measurement = EquipmentMeasurement(
        parameter_id=parameter.id,
        value=value,
        unit=parameter.unit,
        measured_at=measured_at,
        received_at=received_at,
        sensor_timestamp=sensor_timestamp,
        recorded_by=recorded_by,
        source=source,
        source_type=source_type,
        sensor_api_key_id=sensor_api_key_id,
        idempotency_key=idempotency_key,
    )
    db.add(measurement)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        try:
            existing, existing_run = _existing_measurement(
                db,
                parameter.id,
                value,
                source_type,
                measured_at,
                idempotency_key,
                sensor_timestamp is not None,
            )
        except ValueError as conflict:
            raise conflict from error
        if existing is None:
            raise
        return existing, existing_run, True

    execution_key = (
        f"equipment:{measurement.id}:{parameter.updated_at.isoformat()}"
    )
    try:
        with db.begin_nested():
            run = analyze_equipment_measurement(db, measurement)
    except Exception as error:
        logger.exception(
            "Equipment analysis failed for measurement %s", measurement.id
        )
        failed_at = datetime.now(timezone.utc)
        run = AnalysisRun(
            dataset_id=None,
            device_id=device.id,
            equipment_measurement_id=measurement.id,
            model_config_id=None,
            started_by=recorded_by,
            status="failed",
            execution_type="measurement",
            source_type="equipment",
            execution_key=execution_key,
            total_records=0,
            anomalies_found=0,
            started_at=failed_at,
            completed_at=failed_at,
            error_message=str(error)[:2000],
            metrics={
                "configuration": {
                    "parameter_id": parameter.id,
                    "parameter": parameter.name,
                    "algorithm": parameter.analysis_method,
                },
                "records_processed": 0,
                "anomalies_found": 0,
                "failure": str(error)[:2000],
            },
        )
        db.add(run)
        db.flush()
        details = {
            "device_id": device.id,
            "parameter_id": parameter.id,
            "measurement_id": measurement.id,
            "analysis_run_id": run.id,
            "source_type": source_type,
        }
        notify_roles(
            db,
            {"administrator", "data_analyst"},
            "Equipment analysis failed",
            f"Analysis for {device.name} · {parameter.name} failed: {error}",
            "analysis_failed",
            "analysis",
            run.id,
            details=details,
            dedupe_key=f"analysis-failed:{execution_key}",
        )
        notify_user(
            db,
            device.responsible_user_id,
            "Equipment analysis failed",
            f"Analysis for {device.name} · {parameter.name} failed: {error}",
            "analysis_failed",
            "analysis",
            run.id,
            details=details,
            dedupe_key=f"analysis-failed:{execution_key}",
        )

    db.add(
        AuditLog(
            user_id=recorded_by,
            action="record",
            entity_type="equipment_measurement",
            entity_id=measurement.id,
            details={
                "device_id": device.id,
                "parameter_id": parameter.id,
                "analysis_run_id": run.id,
                "source_type": source_type,
                "sensor_api_key_id": sensor_api_key_id,
            },
        )
    )
    db.commit()
    db.refresh(measurement)
    return measurement, run, False
