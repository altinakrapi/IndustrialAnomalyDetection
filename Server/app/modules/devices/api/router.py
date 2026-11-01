from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
from app.modules.devices.application import service
from app.core.notifications import notify_roles, notify_user
from app.modules.devices.application.schedule import measurement_schedule
from app.modules.devices.application.severity import percentile_severity
from app.modules.devices.application.measurement_severity import measurement_severity
from app.shared.models import (
    AnalysisRun,
    Anomaly,
    AuditLog,
    Dataset,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    MaintenanceOrder,
    User,
)

router = APIRouter()
EDITORS = require_roles("administrator", "data_analyst")
DEVICE_STATUSES = {"active", "inactive", "under_maintenance", "archived"}
RESPONSIBLE_ROLES = {
    "administrator",
    "data_analyst",
    "maintenance_technician",
}


def _severity_recompute(db: Session) -> list[dict]:
    anomalies = list(
        db.scalars(
            select(Anomaly)
            .join(
                EquipmentMeasurement,
                EquipmentMeasurement.id == Anomaly.equipment_measurement_id,
            )
            .join(
                EquipmentParameter,
                EquipmentParameter.id == EquipmentMeasurement.parameter_id,
            )
            .where(Anomaly.archived_at.is_(None))
            .order_by(Anomaly.id)
        )
    )
    projected = []
    for anomaly in anomalies:
        if anomaly.anomaly_type in {"threshold", "flatline"} or (
            anomaly.anomaly_type == "ensemble" and (
                (anomaly.expected_range or {}).get("evidence_type") in {"limit", "flatline"}
            )
        ):
            computed = anomaly.severity
            reason = (
                "Configured limit severity preserved."
                if anomaly.anomaly_type == "threshold"
                else "Flatline severity preserved."
            )
        else:
            measurement = anomaly.equipment_measurement
            earlier_scores = db.scalars(
                select(Anomaly.anomaly_score)
                .join(
                    EquipmentMeasurement,
                    EquipmentMeasurement.id == Anomaly.equipment_measurement_id,
                )
                .where(
                    EquipmentMeasurement.parameter_id == measurement.parameter_id,
                    Anomaly.anomaly_type == anomaly.anomaly_type,
                    EquipmentMeasurement.measured_at < measurement.measured_at,
                    Anomaly.anomaly_score.is_not(None),
                    Anomaly.archived_at.is_(None),
                )
                .order_by(EquipmentMeasurement.measured_at.desc())
            )
            computed, reason = percentile_severity(
                float(anomaly.anomaly_score),
                earlier_scores,
            )
        projected.append({
            "id": anomaly.id,
            "detector": anomaly.anomaly_type,
            "computed_severity": computed,
            "severity_reason": reason,
        })
    return projected


def _measurement_severity_projection(db: Session) -> list[dict]:
    anomalies = list(
        db.scalars(
            select(Anomaly)
            .join(
                EquipmentMeasurement,
                EquipmentMeasurement.id == Anomaly.equipment_measurement_id,
            )
            .where(
                Anomaly.archived_at.is_(None),
                Anomaly.equipment_measurement_id.is_not(None),
            )
            .order_by(EquipmentMeasurement.id, Anomaly.id)
        )
    )
    grouped: dict[int, list[Anomaly]] = {}
    for anomaly in anomalies:
        grouped.setdefault(anomaly.equipment_measurement_id, []).append(anomaly)

    projected = []
    for measurement_id, measurement_anomalies in grouped.items():
        result = measurement_severity(measurement_anomalies)
        projected.append({
            "measurement_id": measurement_id,
            "severity": result["severity"],
            "reason": result["rule"]["description"],
            "rule": result["rule"]["fired"],
            "detectors": result["detectors"],
            "agreeing_detectors": result["agreeing_detectors"],
            "analysis_run_ids": result["analysis_run_ids"],
            "anomaly_ids": result["anomaly_ids"],
        })
    return projected


class DeviceInput(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    device_code: str = Field(min_length=1, max_length=100)
    device_type: str = Field(min_length=1, max_length=100)
    category: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    location: str | None = None
    installation_date: date | None = None
    description: str | None = None
    status: str = "active"
    source_type: Literal["manual", "simulated"] = "manual"
    responsible_user_id: int | None = Field(default=None, gt=0)


def device_payload(device: Device) -> dict:
    return {
        "id": device.id,
        "name": device.name,
        "device_code": device.device_code,
        "device_type": device.device_type,
        "category": device.category,
        "manufacturer": device.manufacturer,
        "model": device.model,
        "serial_number": device.serial_number,
        "location": device.location,
        "installation_date": device.installation_date,
        "description": device.description,
        "status": device.status,
        "source_type": device.source_type,
        "responsible_user_id": device.responsible_user_id,
        "responsible_user_name": (
            device.responsible_user.full_name if device.responsible_user else None
        ),
        "created_at": device.created_at,
    }


def _validate_responsible_user(user_id: int | None, db: Session) -> None:
    if user_id is None:
        return
    responsible_user = db.get(User, user_id)
    if responsible_user is None:
        raise HTTPException(status_code=404, detail="Responsible user not found")
    if (
        not responsible_user.is_active
        or responsible_user.role not in RESPONSIBLE_ROLES
    ):
        raise HTTPException(
            status_code=422,
            detail="The responsible user must be active and authorized to record measurements",
        )


def _require_device_access(device: Device, user: User) -> None:
    if user.role != "administrator" and device.responsible_user_id != user.id:
        raise HTTPException(status_code=404, detail="Device not found")


def _parameter_status(parameter: EquipmentParameter, db: Session) -> dict:
    latest_measurement = db.scalar(
        select(EquipmentMeasurement)
        .where(EquipmentMeasurement.parameter_id == parameter.id)
        .order_by(EquipmentMeasurement.measured_at.desc())
        .limit(1)
    )
    last_run = db.scalar(
        select(AnalysisRun)
        .join(
            EquipmentMeasurement,
            EquipmentMeasurement.id == AnalysisRun.equipment_measurement_id,
        )
        .where(EquipmentMeasurement.parameter_id == parameter.id)
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        .limit(1)
    )
    now = datetime.now(timezone.utc)
    next_due_at, measurement_status = measurement_schedule(
        parameter,
        latest_measurement.measured_at if latest_measurement else None,
        now,
    )
    configuration = (
        (last_run.metrics or {}).get("configuration", {}) if last_run else {}
    )
    return {
        "id": parameter.id,
        "name": parameter.name,
        "unit": parameter.unit,
        "analysis_method": parameter.analysis_method,
        "expected_interval_minutes": parameter.expected_interval_minutes,
        "expected_interval_seconds": parameter.expected_interval_seconds,
        "flatline_reading_count": parameter.flatline_reading_count,
        "flatline_variance_tolerance": parameter.flatline_variance_tolerance,
        "enabled": parameter.enabled,
        "lower_limit": parameter.lower_limit,
        "upper_limit": parameter.upper_limit,
        "warning_low": parameter.warning_low,
        "warning_high": parameter.warning_high,
        "critical_low": parameter.critical_low,
        "critical_high": parameter.critical_high,
        "last_measurement_at": (
            latest_measurement.measured_at if latest_measurement else None
        ),
        "next_due_at": next_due_at,
        "measurement_status": measurement_status,
        "last_analysis_at": (
            last_run.completed_at or last_run.started_at or last_run.created_at
            if last_run
            else None
        ),
        "last_analysis_status": last_run.status if last_run else "not_started",
        "last_analysis_note": configuration.get("analysis_note"),
        "next_analysis_due_at": next_due_at if parameter.enabled else None,
        "analysis_trigger": "on_measurement",
    }


@router.get("")
def list_devices(
    search: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    category: str | None = None,
    location: str | None = None,
    sort_by: str = Query(default="name", pattern="^(name|device_code|location|status|created_at)$"),
    sort_order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return service.list_devices(search=search, status_filter=status_filter, category=category, location=location, sort_by=sort_by, sort_order=sort_order, limit=limit, offset=offset, user=user, db=db)


@router.get("/choices")
def device_choices(
    _: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, list[str]]:
    return service.device_choices(_=_, db=db)


@router.get("/responsible-users")
def responsible_user_choices(
    _: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> list[dict]:
    return service.responsible_user_choices(_=_, db=db)


@router.post("/severity/recompute/dry-run")
def recompute_severity_dry_run(
    _: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    return service.recompute_severity_dry_run(_=_, db=db)


@router.post("/severity/recompute")
def recompute_severity(
    user: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    return service.recompute_severity(user=user, db=db)


@router.post("/measurement-severity/dry-run")
def measurement_severity_dry_run(
    _: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    return service.measurement_severity_dry_run(_=_, db=db)


@router.post("/measurement-severity/store")
def store_measurement_severity(
    user: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    return service.store_measurement_severity(user=user, db=db)


@router.post("/admin/archive-test-data/dry-run")
def archive_test_data_dry_run(
    _: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    return service.archive_test_data_dry_run(_=_, db=db)


@router.post("/admin/archive-test-data")
def archive_test_data(
    user: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    return service.archive_test_data(user=user, db=db)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_device(
    payload: DeviceInput,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    return service.create_device(payload=payload, user=user, db=db)


@router.get("/{device_id}")
def get_device(
    device_id: int,
    analysis_limit: int = Query(default=20, ge=1, le=100),
    analysis_offset: int = Query(default=0, ge=0),
    maintenance_limit: int = Query(default=20, ge=1, le=100),
    maintenance_offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return service.get_device(device_id=device_id, analysis_limit=analysis_limit, analysis_offset=analysis_offset, maintenance_limit=maintenance_limit, maintenance_offset=maintenance_offset, user=user, db=db)


@router.patch("/{device_id}/archive")
def archive_device(
    device_id: int,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    return service.archive_device(device_id=device_id, user=user, db=db)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(
    device_id: int,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> None:
    return service.delete_device(device_id=device_id, user=user, db=db)


@router.patch("/{device_id}")
def update_device(
    device_id: int,
    payload: DeviceInput,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    return service.update_device(device_id=device_id, payload=payload, user=user, db=db)
