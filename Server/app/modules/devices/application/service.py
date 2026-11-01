from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
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
    sort_column = getattr(Device, sort_by)
    query = select(Device).where(Device.is_registered.is_(True))
    if user.role != "administrator":
        query = query.where(Device.responsible_user_id == user.id)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            Device.name.ilike(term)
            | Device.device_code.ilike(term)
            | Device.category.ilike(term)
            | Device.location.ilike(term)
        )
    if status_filter:
        query = query.where(Device.status == status_filter)
    if category:
        query = query.where(Device.category == category)
    if location:
        query = query.where(Device.location == location)
    query = query.order_by(
        sort_column.desc() if sort_order == "desc" else sort_column.asc()
    ).limit(limit).offset(offset)
    return [device_payload(device) for device in db.scalars(query)]


@router.get("/choices")
def device_choices(
    _: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, list[str]]:
    return {
        "categories": sorted(
            value
            for value in db.scalars(
                select(Device.category)
                .where(
                    Device.is_registered.is_(True),
                    Device.category.is_not(None),
                    Device.category != "",
                )
                .distinct()
            )
            if value
        ),
        "locations": sorted(
            value
            for value in db.scalars(
                select(Device.location)
                .where(
                    Device.is_registered.is_(True),
                    Device.location.is_not(None),
                    Device.location != "",
                )
                .distinct()
            )
            if value
        ),
    }


@router.get("/responsible-users")
def responsible_user_choices(
    _: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [
        {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "role": user.role,
        }
        for user in db.scalars(
            select(User)
            .where(
                User.is_active.is_(True),
                User.role.in_(RESPONSIBLE_ROLES),
            )
            .order_by(User.full_name)
        )
    ]


@router.post("/severity/recompute/dry-run")
def recompute_severity_dry_run(
    _: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    projected = _severity_recompute(db)
    distribution = {}
    detectors = {}
    for item in projected:
        detector = item["detector"]
        severity = item["computed_severity"]
        distribution[severity] = distribution.get(severity, 0) + 1
        detector_counts = detectors.setdefault(detector, {})
        detector_counts[severity] = detector_counts.get(severity, 0) + 1
    return {
        "dry_run": True,
        "anomalies": len(projected),
        "distribution": distribution,
        "detectors": detectors,
        "minimum_history": 20,
    }


@router.post("/severity/recompute")
def recompute_severity(
    user: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    projected = _severity_recompute(db)
    for item in projected:
        anomaly = db.get(Anomaly, item["id"])
        if anomaly is None:
            continue
        anomaly.computed_severity = item["computed_severity"]
        anomaly.severity_reason = item["severity_reason"]
    db.add(AuditLog(
        user_id=user.id,
        action="recompute",
        entity_type="anomaly_severity",
        entity_id=None,
        details={
            "anomaly_count": len(projected),
            "minimum_history": 20,
        },
    ))
    db.commit()
    distribution = {}
    detectors = {}
    for item in projected:
        distribution[item["computed_severity"]] = (
            distribution.get(item["computed_severity"], 0) + 1
        )
        detector_counts = detectors.setdefault(item["detector"], {})
        detector_counts[item["computed_severity"]] = (
            detector_counts.get(item["computed_severity"], 0) + 1
        )
    return {
        "dry_run": False,
        "anomalies": len(projected),
        "distribution": distribution,
        "detectors": detectors,
        "minimum_history": 20,
    }


@router.post("/measurement-severity/dry-run")
def measurement_severity_dry_run(
    _: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    projected = _measurement_severity_projection(db)
    distribution = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    distribution_by_source = {}
    for item in projected:
        measurement = db.get(EquipmentMeasurement, item["measurement_id"])
        source_type = measurement.source_type if measurement else "unknown"
        distribution[item["severity"]] = distribution.get(item["severity"], 0) + 1
        source_counts = distribution_by_source.setdefault(
            source_type, {"critical": 0, "high": 0, "medium": 0, "low": 0}
        )
        source_counts[item["severity"]] = source_counts.get(item["severity"], 0) + 1
    return {
        "dry_run": True,
        "unique_measurements": len(projected),
        "distribution": distribution,
        "distribution_by_source": distribution_by_source,
        "control_chart_lag_readings": 5,
    }


@router.post("/measurement-severity/store")
def store_measurement_severity(
    user: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    projected = _measurement_severity_projection(db)
    for item in projected:
        measurement = db.get(EquipmentMeasurement, item["measurement_id"])
        if measurement is None:
            continue
        measurement.measurement_severity = item["severity"]
        measurement.measurement_severity_reason = item["reason"]
        measurement.measurement_detectors = item["detectors"]
        measurement.measurement_agreeing_detectors = item["agreeing_detectors"]
        measurement.measurement_rule_fired = item["rule"]
        measurement.measurement_analysis_run_ids = item["analysis_run_ids"]
        measurement.measurement_severity_updated_at = datetime.now(timezone.utc)
    db.add(AuditLog(
        user_id=user.id,
        action="store_measurement_severity",
        entity_type="equipment_measurement",
        entity_id=None,
        details={"unique_measurement_count": len(projected)},
    ))
    db.commit()
    return {
        "dry_run": False,
        "unique_measurements": len(projected),
        "stored": len(projected),
    }


@router.post("/admin/archive-test-data/dry-run")
def archive_test_data_dry_run(
    _: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    devices = list(db.scalars(
        select(Device).where(Device.device_code.like("ANOMALY-TEST-%"))
    ))
    device_ids = [device.id for device in devices]
    anomaly_count = (
        db.scalar(
            select(func.count(Anomaly.id))
            .join(
                AnalysisRun,
                AnalysisRun.id == Anomaly.analysis_run_id,
            )
            .where(
                AnalysisRun.device_id.in_(device_ids),
                Anomaly.archived_at.is_(None),
            )
        )
        if device_ids else 0
    ) or 0
    return {
        "dry_run": True,
        "devices": len(devices),
        "device_codes": [device.device_code for device in devices],
        "anomalies": anomaly_count,
    }


@router.post("/admin/archive-test-data")
def archive_test_data(
    user: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    devices = list(db.scalars(
        select(Device).where(
            Device.device_code.like("ANOMALY-TEST-%"),
            Device.status != "archived",
        )
    ))
    device_ids = [device.id for device in devices]
    archived_at = datetime.now(timezone.utc)
    anomaly_count = 0
    for device in devices:
        device.status = "archived"
    if device_ids:
        anomalies = list(db.scalars(
            select(Anomaly)
            .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
            .where(
                AnalysisRun.device_id.in_(device_ids),
                Anomaly.archived_at.is_(None),
            )
        ))
        for anomaly in anomalies:
            anomaly.archived_at = archived_at
        anomaly_count = len(anomalies)
    db.add(AuditLog(
        user_id=user.id,
        action="archive",
        entity_type="simulated_test_devices",
        entity_id=None,
        details={
            "device_codes": [device.device_code for device in devices],
            "device_count": len(devices),
            "anomaly_count": anomaly_count,
        },
    ))
    db.commit()
    return {
        "dry_run": False,
        "devices": len(devices),
        "anomalies": anomaly_count,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_device(
    payload: DeviceInput,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    values = payload.model_dump()
    values["device_code"] = values["device_code"].strip()
    values["name"] = values["name"].strip()
    if values["status"] not in DEVICE_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported device status")
    requested_responsible_user_id = values.pop("responsible_user_id")
    responsible_user_id = requested_responsible_user_id or user.id
    if (
        requested_responsible_user_id is not None
        and requested_responsible_user_id != user.id
        and user.role != "administrator"
    ):
        raise HTTPException(
            status_code=403,
            detail="Only administrators may assign another responsible user",
        )
    _validate_responsible_user(responsible_user_id, db)
    if db.scalar(select(Device.id).where(Device.device_code == values["device_code"])):
        raise HTTPException(status_code=409, detail="Device code already exists")
    device = Device(
        **values,
        created_by=user.id,
        responsible_user_id=responsible_user_id,
    )
    db.add(device)
    db.flush()
    db.add(AuditLog(user_id=user.id, action="create", entity_type="device", entity_id=device.id))
    if requested_responsible_user_id is not None:
        notify_user(
            db,
            responsible_user_id,
            "Equipment measurement responsibility assigned",
            (
                f"You are responsible for recording actual measurements for "
                f"{device.name}. Review its configured measurement intervals "
                "and due times in the equipment page."
            ),
            "measurement_responsibility_assigned",
            "device",
            device.id,
        )
    notify_roles(
        db,
        {"administrator", "data_analyst", "maintenance_technician"},
        "Equipment registered",
        f"{device.name} ({device.device_code}) was added to the equipment register.",
        "device_created",
        "device",
        device.id,
    )
    db.commit()
    db.refresh(device)
    return device_payload(device)


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
    device = db.get(Device, device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Device not found")
    _require_device_access(device, user)
    result = device_payload(device)
    result["datasets"] = [
        {
            "id": dataset.id,
            "name": dataset.name,
            "row_count": dataset.row_count,
            "status": dataset.status,
            "uploaded_at": dataset.uploaded_at,
        }
        for dataset in db.scalars(
            select(Dataset)
            .where(Dataset.device_id == device.id)
            .order_by(Dataset.uploaded_at.desc())
        )
    ]
    device_analysis_filter = (
        AnalysisRun.device_id == device.id,
        AnalysisRun.source_type == "equipment",
    )
    analysis_count = db.scalar(
        select(func.count(AnalysisRun.id)).where(*device_analysis_filter)
    ) or 0
    analyses = list(
        db.scalars(
            select(AnalysisRun)
            .where(*device_analysis_filter)
            .order_by(AnalysisRun.created_at.desc())
            .limit(analysis_limit)
            .offset(analysis_offset)
        )
    )
    result["analyses"] = [
        {
            "id": run.id,
            "dataset_id": run.dataset_id,
            "dataset_name": run.dataset.name if run.dataset else None,
            "device_id": device.id,
            "device_name": device.name,
            "equipment_measurement_id": run.equipment_measurement_id,
            "source_type": run.source_type,
            "model_config_id": run.model_config_id,
            "model_name": run.model_config.name if run.model_config else None,
            "algorithm": (
                run.model_config.algorithm
                if run.model_config
                else run.metrics.get("configuration", {}).get("algorithm")
            ),
            "status": run.status,
            "execution_type": run.execution_type,
            "source_start_row": run.source_start_row,
            "source_end_row": run.source_end_row,
            "total_records": run.total_records,
            "anomalies_found": run.anomalies_found,
            "metrics": run.metrics,
            "error_message": run.error_message,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "created_at": run.created_at,
        }
        for run in analyses
    ]
    result["analysis_offset"] = analysis_offset
    result["analysis_limit"] = analysis_limit
    result["analysis_total"] = analysis_count
    result["anomaly_count"] = db.scalar(
        select(func.count(Anomaly.id))
        .join(AnalysisRun)
        .where(
            *device_analysis_filter,
            Anomaly.equipment_measurement_id.is_not(None),
        )
    ) or 0
    result["unreviewed_anomaly_count"] = db.scalar(
        select(func.count(Anomaly.id))
        .join(AnalysisRun)
        .where(
            *device_analysis_filter,
            Anomaly.equipment_measurement_id.is_not(None),
            Anomaly.status.in_(("new", "under_review")),
        )
    ) or 0
    result["maintenance_orders"] = [
        {
            "id": order.id,
            "title": order.title,
            "status": order.status,
            "priority": order.priority,
            "anomaly_id": order.anomaly_id,
            "assigned_to": order.assigned_to,
            "due_date": order.due_date,
            "resolution_notes": order.resolution_notes,
            "created_at": order.created_at,
            "actions": [
                {
                    "id": action.id,
                    "action_description": action.action_description,
                    "findings": action.findings,
                    "recommendations": action.recommendations,
                    "issue_confirmed": action.issue_confirmed,
                    "started_at": action.started_at,
                    "completed_at": action.completed_at,
                    "created_at": action.created_at,
                }
                for action in order.actions
            ],
        }
        for order in db.scalars(
            select(MaintenanceOrder)
            .where(MaintenanceOrder.device_id == device.id)
            .order_by(MaintenanceOrder.created_at.desc())
            .limit(maintenance_limit)
            .offset(maintenance_offset)
        )
    ]
    result["maintenance_offset"] = maintenance_offset
    result["maintenance_limit"] = maintenance_limit
    result["dataset_count"] = len(result["datasets"])
    result["analysis_count"] = analysis_count
    parameters = list(
        db.scalars(
            select(EquipmentParameter)
            .where(EquipmentParameter.device_id == device.id)
            .order_by(EquipmentParameter.name)
        )
    )
    result["measurement_parameters"] = [
        _parameter_status(parameter, db)
        for parameter in parameters
    ]
    latest_measurements = list(
        db.scalars(
            select(EquipmentMeasurement)
            .join(EquipmentParameter)
            .where(EquipmentParameter.device_id == device.id)
            .order_by(
                EquipmentMeasurement.measured_at.desc(),
                EquipmentMeasurement.id.desc(),
            )
            .limit(20)
        )
    )
    result["latest_measurements"] = [
        {
            "id": item.id,
            "parameter_id": item.parameter_id,
            "parameter_name": item.parameter.name,
            "value": item.value,
            "unit": item.unit,
            "measured_at": item.measured_at,
            "recorded_at": item.recorded_at,
            "recorded_by": item.recorded_by,
        }
        for item in latest_measurements
    ]
    result["maintenance_order_count"] = db.scalar(
        select(func.count(MaintenanceOrder.id)).where(
            MaintenanceOrder.device_id == device.id
        )
    ) or 0
    return result


@router.patch("/{device_id}/archive")
def archive_device(
    device_id: int,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    device = db.get(Device, device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Device not found")
    _require_device_access(device, user)
    device.status = "archived"
    db.add(
        AuditLog(
            user_id=user.id,
            action="archive",
            entity_type="device",
            entity_id=device.id,
        )
    )
    notify_roles(
        db,
        {"administrator", "data_analyst", "maintenance_technician"},
        "Equipment archived",
        f"{device.name} ({device.device_code}) was archived.",
        "device_archived",
        "device",
        device.id,
    )
    db.commit()
    db.refresh(device)
    return device_payload(device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(
    device_id: int,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> None:
    device = db.get(Device, device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Device not found")
    _require_device_access(device, user)
    has_analysis_history = db.scalar(
        select(AnalysisRun.id)
        .where(AnalysisRun.device_id == device.id)
        .limit(1)
    ) is not None
    has_equipment_parameters = db.scalar(
        select(EquipmentParameter.id)
        .where(EquipmentParameter.device_id == device.id)
        .limit(1)
    ) is not None
    if (
        device.datasets
        or device.maintenance_orders
        or has_analysis_history
        or has_equipment_parameters
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "This device has linked datasets, measurements, analyses, or "
                "maintenance history; archive it instead."
            ),
        )
    db.add(
        AuditLog(
            user_id=user.id,
            action="delete",
            entity_type="device",
            entity_id=device.id,
        )
    )
    notify_roles(
        db,
        {"administrator", "data_analyst", "maintenance_technician"},
        "Equipment removed",
        f"{device.name} ({device.device_code}) was removed from the equipment register.",
        "device_deleted",
        "device",
        device.id,
    )
    db.delete(device)
    db.commit()


@router.patch("/{device_id}")
def update_device(
    device_id: int,
    payload: DeviceInput,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    device = db.get(Device, device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Device not found")
    _require_device_access(device, user)
    previous_responsible_user_id = device.responsible_user_id
    values = payload.model_dump()
    values["device_code"] = values["device_code"].strip()
    if values["status"] not in DEVICE_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported device status")
    if "responsible_user_id" not in payload.model_fields_set:
        values.pop("responsible_user_id")
    else:
        if values["responsible_user_id"] is None:
            raise HTTPException(
                status_code=422,
                detail="Assign an active responsible user before saving equipment",
            )
        if (
            values["responsible_user_id"] != device.responsible_user_id
            and user.role != "administrator"
        ):
            raise HTTPException(
                status_code=403,
                detail="Only administrators may change the responsible user",
            )
        _validate_responsible_user(values["responsible_user_id"], db)
    duplicate = db.scalar(
        select(Device.id).where(
            Device.device_code == values["device_code"],
            Device.id != device_id,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Device code already exists")
    for key, value in values.items():
        setattr(device, key, value.strip() if isinstance(value, str) else value)
    if (
        "responsible_user_id" in values
        and values["responsible_user_id"] != previous_responsible_user_id
    ):
        notify_user(
            db,
            previous_responsible_user_id,
            "Equipment recorder assignment changed",
            f"You are no longer responsible for recording measurements for {device.name}.",
            "measurement_responsibility_changed",
            "device",
            device.id,
        )
        notify_user(
            db,
            device.responsible_user_id,
            "Equipment measurement responsibility assigned",
            (
                f"You are now responsible for recording actual measurements for "
                f"{device.name}. Review its configured measurement intervals "
                "and due times in the equipment page."
            ),
            "measurement_responsibility_assigned",
            "device",
            device.id,
        )
    db.add(AuditLog(user_id=user.id, action="update", entity_type="device", entity_id=device.id))
    notify_roles(
        db,
        {"administrator", "data_analyst", "maintenance_technician"},
        "Equipment updated",
        f"{device.name} ({device.device_code}) was updated.",
        "device_updated",
        "device",
        device.id,
    )
    db.commit()
    db.refresh(device)
    return device_payload(device)