import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
from app.modules.devices.application import equipment_service
from app.modules.devices.application.alerting import (
    alert_setting,
    manual_extreme_score,
)
from app.modules.devices.application.plausibility import physical_measurement_issue
from app.modules.devices.application.measurement_recording import (
    record_equipment_measurement as persist_equipment_measurement,
)
from app.modules.devices.application.schedule import measurement_schedule
from app.shared.models import (
    AnalysisRun,
    Anomaly,
    AuditLog,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    User,
)

router = APIRouter()
ADMINISTRATORS = require_roles("administrator")
MEASUREMENT_USERS = require_roles(
    "administrator", "data_analyst", "maintenance_technician"
)
ANALYSIS_METHODS = {"threshold", "z_score", "isolation_forest", "ensemble"}


def _sync_equipment_service_hooks() -> None:
    for name in (
        "_parameter_payload",
        "_measurement_payload",
        "_registered_device",
        "manual_extreme_score",
        "persist_equipment_measurement",
    ):
        if name in globals():
            setattr(equipment_service, name, globals()[name])


class EquipmentParameterInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=30)
    lower_limit: float | None = None
    upper_limit: float | None = None
    warning_low: float | None = None
    warning_high: float | None = None
    critical_low: float | None = None
    critical_high: float | None = None
    expected_interval_minutes: int = Field(default=120, ge=1, le=525600)
    expected_interval_seconds: int | None = Field(default=None, ge=1, le=31536000)
    analysis_method: str = "isolation_forest"
    z_score_threshold: float = Field(default=3.0, gt=0, le=100)
    isolation_contamination: float = Field(default=0.05, gt=0, le=0.5)
    consecutive_anomaly_threshold: int | None = Field(
        default=None, ge=1, le=100
    )
    persistence_window_size: int | None = Field(default=None, ge=1, le=100)
    persistence_anomaly_threshold: int | None = Field(
        default=None, ge=1, le=100
    )
    flatline_reading_count: int = Field(default=10, ge=2, le=100)
    flatline_variance_tolerance: float = Field(default=0.05, gt=0, le=1)
    enabled: bool = True

    @field_validator(
        "lower_limit",
        "upper_limit",
        "warning_low",
        "warning_high",
        "critical_low",
        "critical_high",
        "z_score_threshold",
        "isolation_contamination",
        "flatline_variance_tolerance",
    )
    @classmethod
    def require_finite_values(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Measurement limits and thresholds must be finite")
        return value

    @field_validator("name", "unit")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("This field cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "EquipmentParameterInput":
        if self.analysis_method not in ANALYSIS_METHODS:
            raise ValueError("Unsupported analysis method")
        if (
            self.lower_limit is not None
            and self.upper_limit is not None
            and self.lower_limit >= self.upper_limit
        ):
            raise ValueError("lower_limit must be less than upper_limit")
        if (
            self.critical_low is not None
            and self.warning_low is not None
            and self.critical_low > self.warning_low
        ):
            raise ValueError("critical_low must not exceed warning_low")
        if (
            self.lower_limit is not None
            and self.critical_low is not None
            and self.lower_limit > self.critical_low
        ):
            raise ValueError("lower_limit must not exceed critical_low")
        if (
            self.warning_low is not None
            and self.upper_limit is not None
            and self.warning_low > self.upper_limit
        ):
            raise ValueError("warning_low must not exceed upper_limit")
        if (
            self.warning_low is not None
            and self.warning_high is not None
            and self.warning_low > self.warning_high
        ):
            raise ValueError("warning_low must not exceed warning_high")
        if (
            self.critical_low is not None
            and self.critical_high is not None
            and self.critical_low > self.critical_high
        ):
            raise ValueError("critical_low must not exceed critical_high")
        if (
            self.warning_high is not None
            and self.critical_high is not None
            and self.warning_high > self.critical_high
        ):
            raise ValueError("warning_high must not exceed critical_high")
        if (
            self.lower_limit is not None
            and self.warning_high is not None
            and self.lower_limit > self.warning_high
        ):
            raise ValueError("lower_limit must not exceed warning_high")
        if (
            self.critical_high is not None
            and self.upper_limit is not None
            and self.critical_high > self.upper_limit
        ):
            raise ValueError("critical_high must not exceed upper_limit")
        if (
            self.persistence_window_size is not None
            and self.persistence_anomaly_threshold is not None
            and self.persistence_anomaly_threshold > self.persistence_window_size
        ):
            raise ValueError(
                "persistence_anomaly_threshold cannot exceed persistence_window_size"
            )
        return self


class EquipmentMeasurementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter_id: int = Field(gt=0)
    value: float
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)
    confirm_extreme: bool = False

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Measurement value must be finite")
        return value

def _parameter_payload(
    parameter: EquipmentParameter, db: Session
) -> dict:
    latest = db.scalar(
        select(EquipmentMeasurement)
        .where(EquipmentMeasurement.parameter_id == parameter.id)
        .order_by(EquipmentMeasurement.measured_at.desc())
        .limit(1)
    )
    count = db.scalar(
        select(func.count(EquipmentMeasurement.id)).where(
            EquipmentMeasurement.parameter_id == parameter.id
        )
    ) or 0
    latest_run = db.scalar(
        select(AnalysisRun)
        .join(
            EquipmentMeasurement,
            EquipmentMeasurement.id == AnalysisRun.equipment_measurement_id,
        )
        .where(EquipmentMeasurement.parameter_id == parameter.id)
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        .limit(1)
    )
    next_due_at, measurement_status = measurement_schedule(
        parameter,
        latest.measured_at if latest else None,
        datetime.now(timezone.utc),
    )
    configuration = (
        (latest_run.metrics or {}).get("configuration", {}) if latest_run else {}
    )
    alert_evaluation = (
        (latest_run.metrics or {}).get("alert_evaluation", {})
        if latest_run
        else {}
    )
    return {
        "id": parameter.id,
        "device_id": parameter.device_id,
        "name": parameter.name,
        "unit": parameter.unit,
        "lower_limit": parameter.lower_limit,
        "upper_limit": parameter.upper_limit,
        "warning_low": parameter.warning_low,
        "warning_high": parameter.warning_high,
        "critical_low": parameter.critical_low,
        "critical_high": parameter.critical_high,
        "expected_interval_minutes": parameter.expected_interval_minutes,
        "expected_interval_seconds": parameter.expected_interval_seconds,
        "last_measurement_at": latest.measured_at if latest else None,
        "next_due_at": next_due_at,
        "measurement_status": measurement_status,
        "analysis_method": parameter.analysis_method,
        "z_score_threshold": parameter.z_score_threshold,
        "isolation_contamination": parameter.isolation_contamination,
        "consecutive_anomaly_threshold": parameter.consecutive_anomaly_threshold,
        "persistence_window_size": parameter.persistence_window_size,
        "persistence_anomaly_threshold": parameter.persistence_anomaly_threshold,
        "flatline_reading_count": parameter.flatline_reading_count,
        "flatline_variance_tolerance": parameter.flatline_variance_tolerance,
        "effective_consecutive_anomaly_threshold": (
            parameter.consecutive_anomaly_threshold
            or int(alert_setting(db, "default_consecutive_anomaly_threshold"))
        ),
        "effective_persistence_window_size": (
            parameter.persistence_window_size
            or int(alert_setting(db, "default_persistence_window_size"))
        ),
        "effective_persistence_anomaly_threshold": (
            parameter.persistence_anomaly_threshold
            or int(alert_setting(db, "default_persistence_anomaly_threshold"))
        ),
        "enabled": parameter.enabled,
        "measurement_count": count,
        "latest_measurement": _measurement_payload(latest) if latest else None,
        "last_analysis_at": (
            latest_run.completed_at or latest_run.started_at or latest_run.created_at
            if latest_run
            else None
        ),
        "last_analysis_status": (
            latest_run.status if latest_run else "not_started"
        ),
        "last_analysis_note": configuration.get("analysis_note"),
        "alert_rules": alert_evaluation.get("alert_rules", []),
        "alert_priority": alert_evaluation.get("priority"),
        "history_state": alert_evaluation.get(
            "history_state",
            configuration.get("history_evaluation", {}).get("state"),
        ),
        "history_size": alert_evaluation.get(
            "history_size",
            configuration.get("history_evaluation", {}).get("history_size"),
        ),
        "history_required": alert_evaluation.get(
            "history_required",
            configuration.get("history_evaluation", {}).get("history_required"),
        ),
        "next_analysis_due_at": next_due_at if parameter.enabled else None,
        "analysis_trigger": "on_measurement",
    }


def _measurement_payload(measurement: EquipmentMeasurement) -> dict:
    parameter = measurement.parameter
    return {
        "id": measurement.id,
        "device_id": parameter.device_id,
        "parameter_id": parameter.id,
        "parameter_name": parameter.name,
        "value": measurement.value,
        "unit": measurement.unit,
        "measured_at": measurement.measured_at,
        "recorded_at": measurement.recorded_at,
        "recorded_by": measurement.recorded_by,
        "recorder_name": (
            measurement.recorder.full_name if measurement.recorder else None
        ),
        "source": measurement.source,
        "source_type": measurement.source_type,
        "received_at": measurement.received_at,
        "sensor_timestamp": measurement.sensor_timestamp,
    }


def _registered_device(
    device_id: int, db: Session, user: User | None = None
) -> Device:
    device = db.get(Device, device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Registered equipment not found")
    if (
        user is not None
        and user.role != "administrator"
        and device.responsible_user_id != user.id
    ):
        raise HTTPException(status_code=404, detail="Registered equipment not found")
    return device


@router.get("/{device_id}/parameters")
def list_equipment_parameters(
    device_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    _sync_equipment_service_hooks()
    return equipment_service.list_equipment_parameters(device_id=device_id, user=user, db=db)


@router.post(
    "/{device_id}/parameters", status_code=status.HTTP_201_CREATED
)
def create_equipment_parameter(
    device_id: int,
    payload: EquipmentParameterInput,
    user: User = Depends(MEASUREMENT_USERS),
    db: Session = Depends(get_db),
) -> dict:
    _sync_equipment_service_hooks()
    return equipment_service.create_equipment_parameter(device_id=device_id, payload=payload, user=user, db=db)


@router.patch("/{device_id}/parameters/{parameter_id}")
def update_equipment_parameter(
    device_id: int,
    parameter_id: int,
    payload: EquipmentParameterInput,
    user: User = Depends(MEASUREMENT_USERS),
    db: Session = Depends(get_db),
) -> dict:
    _sync_equipment_service_hooks()
    return equipment_service.update_equipment_parameter(device_id=device_id, parameter_id=parameter_id, payload=payload, user=user, db=db)


@router.get("/{device_id}/measurements")
def list_equipment_measurements(
    device_id: int,
    parameter_id: int | None = Query(default=None, gt=0),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _sync_equipment_service_hooks()
    return equipment_service.list_equipment_measurements(device_id=device_id, parameter_id=parameter_id, start_time=start_time, end_time=end_time, limit=limit, offset=offset, user=user, db=db)


@router.post("/{device_id}/measurements", status_code=status.HTTP_201_CREATED)
def record_equipment_measurement(
    device_id: int,
    payload: EquipmentMeasurementInput,
    user: User = Depends(MEASUREMENT_USERS),
    db: Session = Depends(get_db),
) -> dict:
    _sync_equipment_service_hooks()
    return equipment_service.record_equipment_measurement(device_id=device_id, payload=payload, user=user, db=db)
