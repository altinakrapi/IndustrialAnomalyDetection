import csv
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.api.dependencies import get_current_user, get_db, require_roles
from app.core.system_settings import get_system_setting
from app.modules.analyses.domain.detectors import (
    DETECTOR_REGISTRY,
    detector_catalog,
    normalize_algorithm_name,
    resolve_ensemble_features,
)
from app.modules.analyses.domain.run_kinds import (
    RunKind,
    analysis_run_kind_expression,
    classify_analysis_run,
)
from app.modules.analyses.api.simulator_evaluations import router as simulator_evaluations
from app.modules.analyses.api.simulator_tuning import router as simulator_tuning
from app.shared.feature_selection import numeric_analysis_features
from app.shared.models import (
    AnalysisRun,
    AnalysisResult,
    AnalysisComparison,
    AuditLog,
    Anomaly,
    Dataset,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    MonitoringSchedule,
    ModelConfig,
    MeasurementRecord,
    SimulatorTunedConfig,
    User,
)
from app.workers.analysis_worker import run_analysis

router = APIRouter()
ANALYSTS = require_roles("administrator", "data_analyst")
router.include_router(simulator_tuning, prefix="/evaluations", tags=["simulator tuning"])
router.include_router(
    simulator_evaluations, prefix="/evaluations", tags=["simulator evaluation"]
)


class ModelConfigInput(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    algorithm: str = "isolation_forest"
    description: str | None = None
    n_estimators: int | None = Field(default=None, ge=10, le=1000)
    contamination: float | None = Field(default=None, gt=0, le=0.5)
    features: list[str] = []
    missing_values_strategy: str = "median"
    scaling: str = "standard"
    threshold: float = Field(default=3.0, gt=0)
    max_samples: int | Literal["auto"] = "auto"
    max_features: float = Field(default=1.0, gt=0, le=1)
    bootstrap: bool = False
    n_jobs: int = Field(default=-1, ge=-1, le=64)
    detector_parameters: dict[str, Any] = Field(default_factory=dict)


class AnalysisInput(BaseModel):
    dataset_id: int
    model_config_id: int
    features: list[str] | None = None
    time_column: str | None = None
    missing_values_strategy: str | None = None
    scaling: str | None = None


class EquipmentHistoryAnalysisInput(BaseModel):
    device_id: int = Field(gt=0)
    parameter_id: int = Field(gt=0)
    algorithm: str
    detector_parameters: dict[str, Any] = Field(default_factory=dict)
    source_type: Literal["manual", "sensor", "simulated"]
    training_window: int = Field(default=100, ge=8, le=5000)
    refit_every: int = Field(default=25, ge=1, le=5000)
    max_readings: int = Field(default=500, ge=1, le=5000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=80)
    received_after: datetime | None = None
    received_before: datetime | None = None


class AnalysisComparisonInput(BaseModel):
    analysis_ids: list[int] = Field(min_length=2, max_length=10)


def model_payload(config: ModelConfig) -> dict:
    return {
        "id": config.id,
        "name": config.name,
        "algorithm": config.algorithm,
        "description": config.description,
        "parameters": config.parameters,
        "contamination": config.contamination,
        "is_active": config.is_active,
        "created_at": config.created_at,
    }


def analysis_payload(analysis: AnalysisRun) -> dict:
    duration_seconds = analysis.metrics.get("execution_seconds")
    if duration_seconds is None and analysis.started_at and analysis.completed_at:
        duration_seconds = max(
            0.0, (analysis.completed_at - analysis.started_at).total_seconds()
        )
    device = analysis.device if analysis.device and analysis.device.is_registered else None
    if device is None and analysis.dataset and analysis.dataset.device:
        if analysis.dataset.device.is_registered:
            device = analysis.dataset.device
    configuration = analysis.metrics.get("configuration", {})
    return {
        "id": analysis.id,
        "dataset_id": analysis.dataset_id,
        "dataset_name": analysis.dataset.name if analysis.dataset else None,
        "device_id": device.id if device else None,
        "device_name": device.name if device else None,
        "equipment_measurement_id": analysis.equipment_measurement_id,
        "source_type": analysis.source_type,
        "model_config_id": analysis.model_config_id,
        "model_name": (
            analysis.model_config.name
            if analysis.model_config
            else configuration.get("parameter")
        ),
        "algorithm": (
            analysis.model_config.algorithm
            if analysis.model_config
            else configuration.get("algorithm")
        ),
        "status": analysis.status,
        "execution_type": analysis.execution_type,
        "run_kind": classify_analysis_run(analysis),
        "source_start_row": analysis.source_start_row,
        "source_end_row": analysis.source_end_row,
        "total_records": analysis.total_records,
        "anomalies_found": analysis.anomalies_found,
        "metrics": analysis.metrics,
        "error_message": analysis.error_message,
        "started_at": analysis.started_at,
        "completed_at": analysis.completed_at,
        "duration_seconds": duration_seconds,
        "created_at": analysis.created_at,
    }


@router.get("/models")
def list_model_configs(
    _: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict]:
    configs = list(db.scalars(select(ModelConfig).order_by(ModelConfig.created_at.desc())))
    used_counts = dict(
        db.execute(
            select(AnalysisRun.model_config_id, func.count(AnalysisRun.id))
            .where(AnalysisRun.model_config_id.is_not(None))
            .group_by(AnalysisRun.model_config_id)
        ).all()
    )
    tuned_configs = {
        config.model_config_id: config
        for config in db.scalars(
            select(SimulatorTunedConfig).where(
                SimulatorTunedConfig.model_config_id.is_not(None)
            )
        )
    }
    return [
        {
            **model_payload(config),
            "used_in_runs": used_counts.get(config.id, 0),
            "source": (
                "tuned_from_development_evidence"
                if config.id in tuned_configs
                else "saved_configuration"
            ),
            "tuning_limitations": (
                tuned_configs[config.id].limitations
                if config.id in tuned_configs
                else None
            ),
            "is_superseded": (
                tuned_configs[config.id].is_superseded
                if config.id in tuned_configs
                else False
            ),
            "superseded_reason": (
                tuned_configs[config.id].superseded_reason
                if config.id in tuned_configs
                else None
            ),
        }
        for config in configs
    ]


@router.get("/detectors")
def list_detectors(_: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return detector_catalog()


@router.post("/models", status_code=status.HTTP_201_CREATED)
def create_model_config(
    payload: ModelConfigInput,
    user: User = Depends(ANALYSTS),
    db: Session = Depends(get_db),
) -> dict:
    algorithm = normalize_algorithm_name(payload.algorithm)
    detector_class = DETECTOR_REGISTRY.get(algorithm)
    if detector_class is None:
        raise HTTPException(status_code=422, detail="Choose a registered analysis detector")
    if payload.missing_values_strategy not in {"drop", "mean", "median"}:
        raise HTTPException(status_code=422, detail="Unsupported missing-value strategy")
    if payload.scaling not in {"standard", "none"}:
        raise HTTPException(status_code=422, detail="Scaling must be standard or none")
    if payload.features is not None and len(set(payload.features)) != len(payload.features):
        raise HTTPException(status_code=422, detail="Feature names must be unique")
    detector_parameters = dict(payload.detector_parameters)
    if algorithm == "isolation_forest":
        detector_parameters.update(
            {
                "n_estimators": payload.n_estimators or int(
                    get_system_setting(db, "default_n_estimators", 100)
                ),
                "contamination": (
                    payload.contamination
                    if payload.contamination is not None
                    else get_system_setting(db, "default_contamination", "auto")
                ),
                "max_samples": payload.max_samples,
                "max_features": payload.max_features,
                "bootstrap": payload.bootstrap,
                "n_jobs": payload.n_jobs,
            }
        )
    elif algorithm == "z_score":
        detector_parameters.setdefault("threshold", payload.threshold)
    try:
        validated_parameters = detector_class.validate_config(detector_parameters)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    parameters = {
        **validated_parameters,
        "features": payload.features,
        "missing_values_strategy": payload.missing_values_strategy,
        "scaling": payload.scaling,
    }
    config = ModelConfig(
        name=payload.name.strip(),
        algorithm=algorithm,
        description=payload.description,
        parameters=parameters,
        contamination=payload.contamination,
        created_by=user.id,
    )
    db.add(config)
    db.flush()
    db.add(AuditLog(user_id=user.id, action="create", entity_type="model_config", entity_id=config.id))
    db.commit()
    db.refresh(config)
    return model_payload(config)


@router.patch("/models/{model_id}")
def update_model_config(
    model_id: int,
    is_active: bool,
    user: User = Depends(require_roles("administrator")),
    db: Session = Depends(get_db),
) -> dict:
    config = db.get(ModelConfig, model_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Model configuration not found")
    config.is_active = is_active
    db.add(
        AuditLog(
            user_id=user.id,
            action="update",
            entity_type="model_config",
            entity_id=config.id,
            details={"is_active": is_active},
        )
    )
    db.commit()
    db.refresh(config)
    return model_payload(config)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_analysis(
    payload: AnalysisInput,
    background_tasks: BackgroundTasks,
    user: User = Depends(ANALYSTS),
    db: Session = Depends(get_db),
) -> dict:
    dataset = db.get(Dataset, payload.dataset_id)
    config = db.get(ModelConfig, payload.model_config_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if dataset.status == "archived":
        raise HTTPException(status_code=409, detail="Archived datasets cannot be analyzed")
    if config is None or not config.is_active:
        raise HTTPException(status_code=404, detail="Active model configuration not found")
    numeric_columns = set(
        numeric_analysis_features(dataset.file_format, dataset.column_metadata)
    )
    features = payload.features or config.parameters.get("features") or sorted(numeric_columns)
    invalid_features = [feature for feature in features if feature not in numeric_columns]
    if not features or invalid_features:
        raise HTTPException(
            status_code=422,
            detail=(
                "Select one or more numeric features available in this dataset"
                if not features
                else f"Selected features are missing or non-numeric: {invalid_features}"
            ),
        )
    available_columns = {column["name"] for column in dataset.column_metadata}
    if payload.time_column and payload.time_column not in available_columns:
        raise HTTPException(status_code=422, detail="Time column is not present in this dataset")
    missing_strategy = (
        payload.missing_values_strategy
        or config.parameters.get("missing_values_strategy", "median")
    )
    scaling = payload.scaling or config.parameters.get("scaling", "standard")
    if missing_strategy not in {"drop", "mean", "median"}:
        raise HTTPException(status_code=422, detail="Unsupported missing-value strategy")
    if scaling not in {"standard", "none"}:
        raise HTTPException(status_code=422, detail="Scaling must be standard or none")
    if normalize_algorithm_name(config.algorithm) == "ensemble":
        try:
            resolve_ensemble_features(config.parameters, features)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    analysis = AnalysisRun(
        dataset_id=dataset.id,
        model_config_id=config.id,
        started_by=user.id,
        status="pending",
        metrics={
            "configuration": {
                "model_config_id": config.id,
                "model_name": config.name,
                "algorithm": config.algorithm,
                "parameters": config.parameters,
                "features": features,
                "time_column": payload.time_column,
                "missing_values_strategy": missing_strategy,
                "scaling": scaling,
            }
        },
    )
    db.add(analysis)
    db.flush()
    db.add(AuditLog(user_id=user.id, action="start", entity_type="analysis", entity_id=analysis.id))
    db.commit()
    db.refresh(analysis)
    background_tasks.add_task(run_analysis, analysis.id)
    return analysis_payload(analysis)


@router.post("/equipment-history", status_code=status.HTTP_202_ACCEPTED)
def create_equipment_history_analysis(
    payload: EquipmentHistoryAnalysisInput,
    background_tasks: BackgroundTasks,
    user: User = Depends(ANALYSTS),
    db: Session = Depends(get_db),
) -> dict:
    device = db.get(Device, payload.device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Registered device not found")
    if user.role != "administrator" and device.responsible_user_id != user.id:
        raise HTTPException(status_code=404, detail="Registered device not found")
    parameter = db.get(EquipmentParameter, payload.parameter_id)
    if (
        parameter is None
        or parameter.device_id != device.id
        or not parameter.enabled
    ):
        raise HTTPException(
            status_code=404, detail="Enabled device parameter not found"
        )
    if payload.source_type == "simulated" and device.source_type != "simulated":
        raise HTTPException(
            status_code=422,
            detail="Simulated measurements require a simulated device",
        )
    algorithm = normalize_algorithm_name(payload.algorithm)
    detector_class = DETECTOR_REGISTRY.get(algorithm)
    if detector_class is None:
        raise HTTPException(status_code=422, detail="Choose a registered analysis detector")
    try:
        detector_parameters = detector_class.validate_config(
            payload.detector_parameters
        )
        if algorithm == "ensemble":
            resolve_ensemble_features(detector_parameters, [parameter.name])
        minimum_history = detector_class.minimum_history(detector_parameters)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if payload.training_window < minimum_history:
        raise HTTPException(
            status_code=422,
            detail="training_window must be at least the detector minimum history",
        )
    if payload.max_readings < minimum_history:
        raise HTTPException(
            status_code=422,
            detail="max_readings must be at least the detector minimum history",
        )
    if any(
        timestamp is not None
        and (timestamp.tzinfo is None or timestamp.utcoffset() is None)
        for timestamp in (payload.received_after, payload.received_before)
    ):
        raise HTTPException(
            status_code=422,
            detail="Measurement receive-time bounds must include a timezone",
        )
    if (
        payload.received_after is not None
        and payload.received_before is not None
        and payload.received_after >= payload.received_before
    ):
        raise HTTPException(
            status_code=422,
            detail="received_after must be earlier than received_before",
        )
    measurement_query = select(EquipmentMeasurement).where(
        EquipmentMeasurement.parameter_id == parameter.id,
        EquipmentMeasurement.source_type == payload.source_type,
    )
    if payload.received_after is not None:
        measurement_query = measurement_query.where(
            EquipmentMeasurement.received_at >= payload.received_after
        )
    if payload.received_before is not None:
        measurement_query = measurement_query.where(
            EquipmentMeasurement.received_at <= payload.received_before
        )
    measurements = list(
        db.scalars(
            measurement_query.order_by(
                EquipmentMeasurement.measured_at.desc(),
                EquipmentMeasurement.id.desc(),
            )
            .limit(payload.max_readings)
        )
    )
    if not measurements:
        raise HTTPException(
            status_code=422,
            detail="No measurements exist for this parameter and source type",
        )
    measurements.reverse()
    configuration = {
        "algorithm": algorithm,
        "parameter_id": parameter.id,
        "parameter": parameter.name,
        "data_source_type": payload.source_type,
        "measurement_ids": [measurement.id for measurement in measurements],
        "detector_parameters": detector_parameters,
        "training_window": payload.training_window,
        "refit_every": payload.refit_every,
        "minimum_history": minimum_history,
        "execution_mode": "walk_forward",
        "ordering": "measured_at ascending, then measurement id ascending",
        "time_basis": "measured_at; server receive time when no sensor timestamp is supplied",
        "received_after": (
            payload.received_after.isoformat()
            if payload.received_after is not None
            else None
        ),
        "received_before": (
            payload.received_before.isoformat()
            if payload.received_before is not None
            else None
        ),
    }
    execution_key = None
    if payload.idempotency_key is not None:
        identity = json.dumps(
            {
                "user_id": user.id,
                "device_id": device.id,
                "parameter_id": parameter.id,
                "configuration": configuration,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        execution_key = "equipment-history:" + hashlib.sha256(
            f"{payload.idempotency_key}:{identity}".encode("utf-8")
        ).hexdigest()
        existing = db.scalar(
            select(AnalysisRun).where(
                AnalysisRun.execution_key == execution_key
            )
        )
        if existing is not None:
            return analysis_payload(existing)
    analysis = AnalysisRun(
        device_id=device.id,
        model_config_id=None,
        started_by=user.id,
        status="pending",
        execution_type="manual",
        source_type="equipment",
        execution_key=execution_key,
        total_records=len(measurements),
        metrics={
            "configuration": configuration,
            "source_type": payload.source_type,
        },
    )
    db.add(analysis)
    db.flush()
    db.add(
        AuditLog(
            user_id=user.id,
            action="start",
            entity_type="analysis",
            entity_id=analysis.id,
            details={
                "device_id": device.id,
                "parameter_id": parameter.id,
                "source_type": payload.source_type,
                "algorithm": algorithm,
                "records_selected": len(measurements),
            },
        )
    )
    db.commit()
    db.refresh(analysis)
    from app.workers.equipment_history_analysis_worker import (
        run_equipment_history_analysis,
    )

    background_tasks.add_task(run_equipment_history_analysis, analysis.id)
    return analysis_payload(analysis)


@router.get("")
def list_analyses(
    status_filter: str | None = None,
    dataset_id: int | None = None,
    device_id: int | None = None,
    algorithm: str | None = None,
    execution_type: str | None = None,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    sort_by: str = Query(default="created_at", pattern="^(created_at|started_at|completed_at|anomalies_found|total_records|status)$"),
    sort_direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(AnalysisRun)
    if status_filter:
        query = query.where(AnalysisRun.status == status_filter)
    if dataset_id:
        query = query.where(AnalysisRun.dataset_id == dataset_id)
    if device_id:
        query = query.where(
            or_(
                AnalysisRun.device_id == device_id,
                AnalysisRun.dataset.has(Dataset.device_id == device_id),
            )
        )
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            AnalysisRun.dataset.has(Dataset.name.ilike(term))
            | AnalysisRun.device.has(Device.name.ilike(term))
            | AnalysisRun.status.ilike(term)
        )
    if start_date:
        query = query.where(AnalysisRun.created_at >= start_date)
    if end_date:
        query = query.where(AnalysisRun.created_at <= end_date)
    if algorithm:
        query = query.where(
            or_(
                AnalysisRun.model_config.has(ModelConfig.algorithm == algorithm),
                AnalysisRun.metrics["configuration"]["algorithm"].as_string()
                == algorithm,
            )
        )
    if execution_type:
        query = query.where(AnalysisRun.execution_type == execution_type)
    sort_column = getattr(AnalysisRun, sort_by)
    query = query.order_by(
        sort_column.desc() if sort_direction == "desc" else sort_column.asc(),
        AnalysisRun.id.desc(),
    )
    query = query.limit(limit).offset(offset)
    return [analysis_payload(analysis) for analysis in db.scalars(query)]


@router.get("/summary")
def analysis_summary(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    def population(source_type: str, measurement_column: Any) -> dict[str, Any]:
        latest = db.scalar(
            select(AnalysisRun)
            .where(AnalysisRun.source_type == source_type)
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .limit(1)
        )
        return {
            "analyses": db.scalar(
                select(func.count(AnalysisRun.id)).where(
                    AnalysisRun.source_type == source_type
                )
            ) or 0,
            "detections": db.scalar(
                select(func.count(Anomaly.id)).where(
                    measurement_column.is_not(None)
                )
            ) or 0,
            "measurements_flagged_unique": db.scalar(
                select(func.count(func.distinct(measurement_column))).where(
                    measurement_column.is_not(None)
                )
            ) or 0,
            "latest_execution": analysis_payload(latest) if latest else None,
        }

    status_counts = dict(
        db.execute(
            select(AnalysisRun.status, func.count(AnalysisRun.id))
            .group_by(AnalysisRun.status)
        ).all()
    )
    latest = db.scalar(
        select(AnalysisRun).order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc()).limit(1)
    )
    schedule = db.get(MonitoringSchedule, 1)
    live_reading_filter = and_(
        AnalysisRun.source_type == "equipment",
        AnalysisRun.execution_type == "measurement",
        AnalysisRun.total_records == 1,
        AnalysisRun.created_at >= datetime.now(timezone.utc) - timedelta(hours=24),
    )
    live_reading_counts = db.execute(
        select(
            func.count(AnalysisRun.id),
            func.count(AnalysisRun.id).filter(AnalysisRun.status == "failed"),
        ).where(live_reading_filter)
    ).one()
    registered_equipment = population("equipment", Anomaly.equipment_measurement_id)
    datasets = population("dataset", Anomaly.measurement_record_id)
    return {
        "total_runs": sum(status_counts.values()),
        "completed": status_counts.get("completed", 0),
        "failed": status_counts.get("failed", 0),
        "in_progress": sum(
            status_counts.get(state, 0) for state in ("pending", "running")
        ),
        "anomaly_detections": (
            registered_equipment["detections"] + datasets["detections"]
        ),
        "unique_affected_measurements": (
            registered_equipment["measurements_flagged_unique"]
            + datasets["measurements_flagged_unique"]
        ),
        "latest_analysis": analysis_payload(latest) if latest else None,
        "next_scheduled_at": schedule.next_run_at if schedule and schedule.enabled else None,
        "schedule_enabled": bool(schedule and schedule.enabled),
        "registered_equipment": {
            **registered_equipment,
            "next_scheduled_execution": None,
        },
        "datasets": {
            **datasets,
            "next_scheduled_execution": (
                schedule.next_run_at if schedule and schedule.enabled else None
            ),
        },
        "live_reading_24h": {
            "total": live_reading_counts[0],
            "failed": live_reading_counts[1],
        },
    }


@router.get("/history")
def analysis_history(
    status_filter: str | None = None,
    dataset_id: int | None = None,
    device_id: int | None = None,
    algorithm: str | None = None,
    execution_type: str | None = None,
    source_type: Literal["dataset", "equipment"] | None = None,
    run_kind: RunKind | None = None,
    exclude_live_readings: bool = False,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    sort_by: str = Query(default="created_at", pattern="^(created_at|started_at|completed_at|anomalies_found|total_records|status)$"),
    sort_direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    filters = []
    if status_filter:
        filters.append(AnalysisRun.status == status_filter)
    if dataset_id:
        filters.append(AnalysisRun.dataset_id == dataset_id)
    if device_id:
        filters.append(
            or_(
                AnalysisRun.device_id == device_id,
                AnalysisRun.dataset.has(Dataset.device_id == device_id),
            )
        )
    if algorithm:
        filters.append(
            or_(
                AnalysisRun.model_config.has(ModelConfig.algorithm == algorithm),
                AnalysisRun.metrics["configuration"]["algorithm"].as_string()
                == algorithm,
            )
        )
    if execution_type:
        filters.append(AnalysisRun.execution_type == execution_type)
    if source_type:
        filters.append(AnalysisRun.source_type == source_type)
    run_kind_expression = analysis_run_kind_expression()
    if run_kind:
        filters.append(run_kind_expression == run_kind)
    elif exclude_live_readings:
        filters.append(run_kind_expression != "live_reading")
    if start_date:
        filters.append(AnalysisRun.created_at >= start_date)
    if end_date:
        filters.append(AnalysisRun.created_at <= end_date)
    if search:
        term = f"%{search.strip()}%"
        filters.append(
            AnalysisRun.dataset.has(Dataset.name.ilike(term))
            | AnalysisRun.device.has(Device.name.ilike(term))
            | AnalysisRun.status.ilike(term)
            | AnalysisRun.model_config.has(ModelConfig.name.ilike(term))
            | AnalysisRun.metrics["configuration"]["parameter"].as_string().ilike(term)
        )
    count = db.scalar(
        select(func.count(AnalysisRun.id)).where(*filters)
    ) or 0
    sort_column = getattr(AnalysisRun, sort_by)
    query = (
        select(AnalysisRun)
        .where(*filters)
        .order_by(
            sort_column.desc() if sort_direction == "desc" else sort_column.asc(),
            AnalysisRun.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return {
        "items": [analysis_payload(run) for run in db.scalars(query)],
        "total": count,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{analysis_id}")
def get_analysis(
    analysis_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    analysis = db.get(AnalysisRun, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    result = analysis_payload(analysis)
    result["severity_distribution"] = dict(
        db.execute(
            select(Anomaly.severity, func.count(Anomaly.id))
            .where(Anomaly.analysis_run_id == analysis.id)
            .group_by(Anomaly.severity)
        ).all()
    )
    result["anomaly_count"] = db.scalar(
        select(func.count(Anomaly.id)).where(Anomaly.analysis_run_id == analysis.id)
    ) or 0
    result["unique_affected_measurements"] = (
        db.scalar(
            select(func.count(func.distinct(Anomaly.measurement_record_id))).where(
                Anomaly.analysis_run_id == analysis.id,
                Anomaly.measurement_record_id.is_not(None),
            )
        )
        or 0
    ) + (
        db.scalar(
            select(func.count(func.distinct(Anomaly.equipment_measurement_id))).where(
                Anomaly.analysis_run_id == analysis.id,
                Anomaly.equipment_measurement_id.is_not(None),
            )
        )
        or 0
    )
    result["algorithm_flagged_results"] = db.scalar(
        select(func.count(AnalysisResult.id)).where(
            AnalysisResult.analysis_run_id == analysis.id,
            AnalysisResult.is_anomaly.is_(True),
        )
    ) or 0
    result["configuration"] = analysis.metrics.get("configuration", {})
    return result


@router.get("/{analysis_id}/results")
def list_analysis_results(
    analysis_id: int,
    is_anomaly: bool | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    analysis = db.get(AnalysisRun, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    query = select(AnalysisResult).where(
        AnalysisResult.analysis_run_id == analysis_id
    )
    count_query = select(func.count(AnalysisResult.id)).where(
        AnalysisResult.analysis_run_id == analysis_id
    )
    if is_anomaly is not None:
        anomaly_filter = AnalysisResult.is_anomaly.is_(is_anomaly)
        query = query.where(anomaly_filter)
        count_query = count_query.where(anomaly_filter)
    query = query.order_by(
        AnalysisResult.source_row_number,
        AnalysisResult.equipment_measurement_id,
    ).limit(limit).offset(offset)
    records = list(db.scalars(query))
    total = db.scalar(count_query) or 0
    timestamps = {
        item.id: item.timestamp
        for item in db.scalars(
            select(MeasurementRecord).where(
                MeasurementRecord.id.in_(
                    [record.measurement_record_id for record in records if record.measurement_record_id]
                )
            )
        )
    } if records else {}
    equipment_times = {
        measurement.id: measurement.measured_at
        for measurement in db.scalars(
            select(EquipmentMeasurement).where(
                EquipmentMeasurement.id.in_(
                    [
                        record.equipment_measurement_id
                        for record in records
                        if record.equipment_measurement_id
                    ]
                )
            )
        )
    } if records else {}
    return {
        "analysis_id": analysis.id,
        "limit": limit,
        "offset": offset,
        "total": total,
        "results": [
            {
                "id": record.id,
                "measurement_record_id": record.measurement_record_id,
                "equipment_measurement_id": record.equipment_measurement_id,
                "source_row_number": record.source_row_number,
                "timestamp": (
                    timestamps.get(record.measurement_record_id)
                    if record.measurement_record_id is not None
                    else equipment_times.get(record.equipment_measurement_id)
                ),
                "is_anomaly": record.is_anomaly,
                "anomaly_score": record.anomaly_score,
                "history_state": record.history_state,
                "history_size": record.history_size,
                "history_required": record.history_required,
                "result_details": record.result_details,
                "feature_values": record.feature_values,
            }
            for record in records
        ],
    }


@router.get("/{analysis_id}/timeline")
def analysis_timeline(
    analysis_id: int,
    feature: str,
    time_column: str | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    analysis = db.get(AnalysisRun, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if analysis.status != "completed":
        raise HTTPException(status_code=409, detail="Timeline is available after analysis completes")
    configuration = analysis.metrics.get("configuration", {})
    selected_time_column = time_column or configuration.get("time_column")
    if selected_time_column not in {"timestamp", None}:
        raise HTTPException(status_code=422, detail="Choose a valid time column")
    if analysis.source_type == "equipment":
        parameter_name = configuration.get("parameter")
        if feature not in {"value", parameter_name}:
            raise HTTPException(
                status_code=422,
                detail="Choose the equipment parameter value included in this analysis",
            )
        count = db.scalar(
            select(func.count(AnalysisResult.id)).where(
                AnalysisResult.analysis_run_id == analysis.id
            )
        ) or 0
        step = max(1, (count + limit - 1) // limit)
        ordered_results = (
            select(
                AnalysisResult.id.label("result_id"),
                AnalysisResult.is_anomaly.label("is_anomaly"),
                AnalysisResult.anomaly_score.label("anomaly_score"),
                EquipmentMeasurement.id.label("measurement_id"),
                EquipmentMeasurement.measured_at.label("measured_at"),
                EquipmentMeasurement.value.label("value"),
                func.row_number()
                .over(
                    order_by=(
                        EquipmentMeasurement.measured_at,
                        EquipmentMeasurement.id,
                    )
                )
                .label("ordinal"),
            )
            .join(
                EquipmentMeasurement,
                AnalysisResult.equipment_measurement_id == EquipmentMeasurement.id,
            )
            .where(AnalysisResult.analysis_run_id == analysis.id)
            .subquery()
        )
        rows = list(
            db.execute(
                select(ordered_results)
                .where(func.mod(ordered_results.c.ordinal - 1, step) == 0)
                .order_by(ordered_results.c.measured_at)
                .limit(limit)
            )
        )
        sampled_ids = {row.measurement_id for row in rows}
        anomaly_rows = list(
            db.execute(
                select(ordered_results)
                .where(
                    ordered_results.c.is_anomaly.is_(True),
                    ~ordered_results.c.measurement_id.in_(sampled_ids)
                    if sampled_ids
                    else True,
                )
                .order_by(ordered_results.c.measured_at)
                .limit(min(limit, 500))
            )
        )
        selected_rows = sorted(
            {row.measurement_id: row for row in (*rows, *anomaly_rows)}.values(),
            key=lambda row: row.measured_at,
        )
        return {
            "analysis_id": analysis.id,
            "feature": feature,
            "time_column": "measured_at",
            "points": [
                {
                    "source_row_number": None,
                    "measurement_id": row.measurement_id,
                    "timestamp": row.measured_at.isoformat(),
                    "value": row.value,
                    "anomaly_score": row.anomaly_score,
                    "is_anomaly": row.is_anomaly,
                }
                for row in selected_rows
            ],
        }
    if feature not in configuration.get("features", []):
        raise HTTPException(status_code=422, detail="Feature was not included in this analysis")
    count = db.scalar(
        select(func.count(AnalysisResult.id)).where(
            AnalysisResult.analysis_run_id == analysis.id
        )
    ) or 0
    step = max(1, count // limit)
    query = (
        select(AnalysisResult, MeasurementRecord)
        .join(
            MeasurementRecord,
            AnalysisResult.measurement_record_id == MeasurementRecord.id,
        )
        .where(
            AnalysisResult.analysis_run_id == analysis.id,
            func.mod(AnalysisResult.source_row_number - 1, step) == 0,
        )
        .order_by(AnalysisResult.source_row_number)
        .limit(limit)
    )
    selected = list(db.execute(query))
    sampled_rows = {result.source_row_number for result, _ in selected}
    selected.extend(
        db.execute(
            select(AnalysisResult, MeasurementRecord)
            .join(
                MeasurementRecord,
                AnalysisResult.measurement_record_id == MeasurementRecord.id,
            )
            .where(
                AnalysisResult.analysis_run_id == analysis.id,
                AnalysisResult.is_anomaly.is_(True),
                ~AnalysisResult.source_row_number.in_(sampled_rows),
            )
            .order_by(AnalysisResult.source_row_number)
            .limit(min(limit, 500))
        )
    )
    selected.sort(key=lambda pair: pair[0].source_row_number)
    points = [
        {
            "source_row_number": result.source_row_number,
            "timestamp": record.timestamp.isoformat() if record.timestamp else None,
            "value": result.feature_values.get(feature),
            "is_anomaly": result.is_anomaly,
        }
        for result, record in selected
    ]
    return {
        "analysis_id": analysis.id,
        "feature": feature,
        "time_column": selected_time_column,
        "points": points,
    }


def _compare_run_pair(
    db: Session, left: AnalysisRun, right: AnalysisRun
) -> dict[str, Any] | None:
    left_configuration = left.metrics.get("configuration", {})
    right_configuration = right.metrics.get("configuration", {})
    same_equipment_parameter = (
        left.source_type == right.source_type == "equipment"
        and left.device_id == right.device_id
        and left_configuration.get("parameter_id")
        == right_configuration.get("parameter_id")
    )
    same_dataset_features = (
        left.source_type == right.source_type == "dataset"
        and left.dataset_id == right.dataset_id
        and bool(
            set(left_configuration.get("features", []))
            .intersection(right_configuration.get("features", []))
        )
    )
    if (
        not (same_equipment_parameter or same_dataset_features)
        or left.status != "completed"
        or right.status != "completed"
    ):
        return None
    measurement_fk = (
        "equipment_measurement_id"
        if same_equipment_parameter
        else "measurement_record_id"
    )
    left_result = aliased(AnalysisResult)
    right_result = aliased(AnalysisResult)
    left_filter = (
        left_result.analysis_run_id == left.id,
        left_result.is_anomaly.is_(True),
        getattr(left_result, measurement_fk).is_not(None),
    )
    right_filter = (
        right_result.analysis_run_id == right.id,
        right_result.is_anomaly.is_(True),
        getattr(right_result, measurement_fk).is_not(None),
    )
    shared = db.scalar(
        select(func.count(left_result.id))
        .select_from(left_result)
        .join(
            right_result,
            getattr(left_result, measurement_fk)
            == getattr(right_result, measurement_fk),
        )
        .where(*left_filter, *right_filter)
    ) or 0
    left_only = db.scalar(
        select(func.count(left_result.id)).where(
            *left_filter,
            ~exists(
                select(1).where(
                    *right_filter,
                    getattr(right_result, measurement_fk)
                    == getattr(left_result, measurement_fk),
                )
            ),
        )
    )
    right_only = db.scalar(
        select(func.count(right_result.id)).where(
            *right_filter,
            ~exists(
                select(1).where(
                    *left_filter,
                    getattr(left_result, measurement_fk)
                    == getattr(right_result, measurement_fk),
                )
            ),
        )
    )
    return {
        "comparable": True,
        "matched_by": measurement_fk,
        "other_analysis_id": right.id,
        "identified_by_both": int(shared),
        "identified_only_by_this_run": int(left_only or 0),
        "identified_only_by_other_run": int(right_only or 0),
        "this_run_anomaly_rate": (
            round(left.anomalies_found / left.total_records * 100, 2)
            if left.total_records
            else 0
        ),
        "other_run_anomaly_rate": (
            round(right.anomalies_found / right.total_records * 100, 2)
            if right.total_records
            else 0
        ),
    }


@router.post("/compare")
def compare_analyses(
    analysis_ids: list[int],
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    if len(analysis_ids) < 2 or len(analysis_ids) > 10:
        raise HTTPException(status_code=422, detail="Select between two and ten analyses")
    unique_ids = set(analysis_ids)
    analyses = list(
        db.scalars(select(AnalysisRun).where(AnalysisRun.id.in_(unique_ids)))
    )
    if len(analyses) != len(unique_ids):
        raise HTTPException(status_code=404, detail="One or more analyses were not found")
    comparison_by_id: dict[int, dict] = {}
    for index, left in enumerate(analyses):
        for right in analyses[index + 1 :]:
            left_comparison = _compare_run_pair(db, left, right)
            if left_comparison is None:
                continue
            right_comparison = {
                **left_comparison,
                "other_analysis_id": left.id,
                "identified_only_by_this_run": left_comparison[
                    "identified_only_by_other_run"
                ],
                "identified_only_by_other_run": left_comparison[
                    "identified_only_by_this_run"
                ],
                "this_run_anomaly_rate": left_comparison["other_run_anomaly_rate"],
                "other_run_anomaly_rate": left_comparison["this_run_anomaly_rate"],
            }
            comparison_by_id[left.id] = left_comparison
            comparison_by_id[right.id] = right_comparison
    return [
        {
            "id": item.id,
            "dataset_id": item.dataset_id,
            "dataset_name": item.dataset.name if item.dataset else None,
            "device_id": item.device_id,
            "model_name": (
                item.model_config.name
                if item.model_config
                else item.metrics.get("configuration", {}).get("parameter")
            ),
            "algorithm": (
                item.model_config.algorithm
                if item.model_config
                else item.metrics.get("configuration", {}).get("algorithm")
            ),
            "status": item.status,
            "total_records": item.total_records,
            "anomalies_found": item.anomalies_found,
            "metrics": item.metrics,
            "duration_seconds": item.metrics.get("execution_seconds"),
            "comparison": comparison_by_id.get(
                item.id,
                {
                    "comparable": False,
                    "message": (
                        "Runs need the same dataset, completed status, and at least "
                        "one shared measurement feature for measurement-level comparison."
                    ),
                },
            ),
        }
        for item in analyses
    ]


@router.post("/comparisons", status_code=status.HTTP_201_CREATED)
def create_analysis_comparison(
    payload: AnalysisComparisonInput,
    user: User = Depends(ANALYSTS),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    analysis_ids = list(dict.fromkeys(payload.analysis_ids))
    if len(analysis_ids) < 2:
        raise HTTPException(
            status_code=422, detail="Select at least two distinct analyses"
        )
    analyses = list(
        db.scalars(select(AnalysisRun).where(AnalysisRun.id.in_(analysis_ids)))
    )
    if len(analyses) != len(analysis_ids):
        raise HTTPException(status_code=404, detail="One or more analyses were not found")
    analyses_by_id = {analysis.id: analysis for analysis in analyses}
    ordered_analyses = [analyses_by_id[analysis_id] for analysis_id in analysis_ids]
    if any(analysis.status != "completed" for analysis in ordered_analyses):
        raise HTTPException(
            status_code=422, detail="Only completed analyses can be compared"
        )

    pair_comparisons: dict[tuple[int, int], dict[str, Any]] = {}
    for index, left in enumerate(ordered_analyses):
        for right in ordered_analyses[index + 1 :]:
            comparison = _compare_run_pair(db, left, right)
            if comparison is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Selected analyses must share a dataset feature or the same "
                        "device parameter."
                    ),
                )
            pair_comparisons[(left.id, right.id)] = comparison

    agreement_matrix: list[dict[str, Any]] = []
    for left in ordered_analyses:
        for right in ordered_analyses:
            if left.id == right.id:
                agreement_matrix.append({
                    "analysis_id": left.id,
                    "other_analysis_id": right.id,
                    "comparable": True,
                    "matched_by": "self",
                    "both": left.anomalies_found,
                    "left_only": 0,
                    "right_only": 0,
                })
                continue
            is_forward_pair = (left.id, right.id) in pair_comparisons
            comparison = (
                pair_comparisons[(left.id, right.id)]
                if is_forward_pair
                else pair_comparisons[(right.id, left.id)]
            )
            agreement_matrix.append({
                "analysis_id": left.id,
                "other_analysis_id": right.id,
                "comparable": True,
                "matched_by": comparison["matched_by"],
                "both": comparison["identified_by_both"],
                "left_only": (
                    comparison["identified_only_by_this_run"]
                    if is_forward_pair
                    else comparison["identified_only_by_other_run"]
                ),
                "right_only": (
                    comparison["identified_only_by_other_run"]
                    if is_forward_pair
                    else comparison["identified_only_by_this_run"]
                ),
            })

    run_summaries: list[dict[str, Any]] = []
    for analysis in ordered_analyses:
        result_stats = db.execute(
            select(
                func.count(AnalysisResult.id),
                func.min(AnalysisResult.anomaly_score),
                func.max(AnalysisResult.anomaly_score),
                func.avg(AnalysisResult.anomaly_score),
                func.percentile_cont(0.25).within_group(
                    AnalysisResult.anomaly_score
                ),
                func.percentile_cont(0.5).within_group(
                    AnalysisResult.anomaly_score
                ),
                func.percentile_cont(0.75).within_group(
                    AnalysisResult.anomaly_score
                ),
                func.percentile_cont(0.95).within_group(
                    AnalysisResult.anomaly_score
                ),
            ).where(
                AnalysisResult.analysis_run_id == analysis.id,
                AnalysisResult.anomaly_score.is_not(None),
            )
        ).one()
        history_counts = db.execute(
            select(
                AnalysisResult.history_state,
                func.count(AnalysisResult.id),
            )
            .where(AnalysisResult.analysis_run_id == analysis.id)
            .group_by(AnalysisResult.history_state)
        ).all()
        run_summaries.append({
            "analysis_id": analysis.id,
            "model_name": (
                analysis.model_config.name
                if analysis.model_config
                else analysis.metrics.get("configuration", {}).get("parameter")
            ),
            "algorithm": (
                analysis.model_config.algorithm
                if analysis.model_config
                else analysis.metrics.get("configuration", {}).get("algorithm")
            ),
            "status": analysis.status,
            "total_records": analysis.total_records,
            "anomalies_found": analysis.anomalies_found,
            "anomaly_rate": (
                round(analysis.anomalies_found / analysis.total_records, 6)
                if analysis.total_records
                else 0.0
            ),
            "score_distribution": {
                "count": int(result_stats[0] or 0),
                "min": result_stats[1],
                "max": result_stats[2],
                "mean": float(result_stats[3]) if result_stats[3] is not None else None,
                "p25": result_stats[4],
                "median": result_stats[5],
                "p75": result_stats[6],
                "p95": result_stats[7],
            },
            "runtime_seconds": analysis.metrics.get("execution_seconds"),
            "history_state_counts": {
                state or "unspecified": int(count) for state, count in history_counts
            },
        })

    snapshot = {
        "runs": run_summaries,
        "agreement_matrix": agreement_matrix,
        "interpretation": "Statistical evidence for human review; not a diagnosis.",
    }
    comparison_record = AnalysisComparison(
        created_by=user.id,
        analysis_ids=analysis_ids,
        snapshot=snapshot,
    )
    db.add(comparison_record)
    db.flush()
    db.add(AuditLog(
        user_id=user.id,
        action="create",
        entity_type="analysis_comparison",
        entity_id=comparison_record.id,
        details={"analysis_ids": analysis_ids},
    ))
    db.commit()
    return {"id": comparison_record.id, **snapshot}


@router.get("/comparisons/{comparison_id}")
def get_analysis_comparison(
    comparison_id: int,
    _: User = Depends(ANALYSTS),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    comparison = db.get(AnalysisComparison, comparison_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail="Analysis comparison not found")
    return {
        "id": comparison.id,
        "analysis_ids": comparison.analysis_ids,
        **comparison.snapshot,
        "created_at": comparison.created_at.isoformat(),
    }


@router.get("/comparisons/{comparison_id}/csv")
def export_analysis_comparison(
    comparison_id: int,
    _: User = Depends(ANALYSTS),
    db: Session = Depends(get_db),
) -> Response:
    comparison = db.get(AnalysisComparison, comparison_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail="Analysis comparison not found")
    output = io.StringIO(newline="")
    columns = [
        "record_type",
        "analysis_id",
        "other_analysis_id",
        "algorithm",
        "status",
        "total_records",
        "anomalies_found",
        "anomaly_rate",
        "score_count",
        "score_min",
        "score_max",
        "score_mean",
        "score_p25",
        "score_median",
        "score_p75",
        "score_p95",
        "runtime_seconds",
        "history_state_counts",
        "matched_by",
        "both",
        "left_only",
        "right_only",
    ]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for run in comparison.snapshot.get("runs", []):
        score_distribution = run.get("score_distribution", {})
        writer.writerow({
            "record_type": "run",
            "analysis_id": run.get("analysis_id"),
            "algorithm": run.get("algorithm"),
            "status": run.get("status"),
            "total_records": run.get("total_records"),
            "anomalies_found": run.get("anomalies_found"),
            "anomaly_rate": run.get("anomaly_rate"),
            "score_count": score_distribution.get("count"),
            "score_min": score_distribution.get("min"),
            "score_max": score_distribution.get("max"),
            "score_mean": score_distribution.get("mean"),
            "score_p25": score_distribution.get("p25"),
            "score_median": score_distribution.get("median"),
            "score_p75": score_distribution.get("p75"),
            "score_p95": score_distribution.get("p95"),
            "runtime_seconds": run.get("runtime_seconds"),
            "history_state_counts": json.dumps(
                run.get("history_state_counts", {}), sort_keys=True
            ),
        })
    for pair in comparison.snapshot.get("agreement_matrix", []):
        writer.writerow({
            "record_type": "agreement",
            "analysis_id": pair.get("analysis_id"),
            "other_analysis_id": pair.get("other_analysis_id"),
            "matched_by": pair.get("matched_by"),
            "both": pair.get("both"),
            "left_only": pair.get("left_only"),
            "right_only": pair.get("right_only"),
        })
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="analysis-comparison-{comparison.id}.csv"'
            )
        },
    )