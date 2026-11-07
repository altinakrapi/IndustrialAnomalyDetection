from datetime import datetime, timezone
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
from app.core.notifications import notify_user
from app.shared.models import (
    AnalysisRun,
    Anomaly,
    AnomalyFeedback,
    AnomalyFeedbackHistory,
    AnomalyReview,
    AuditLog,
    Dataset,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    GeneratedReport,
    MaintenanceOrder,
    MeasurementRecord,
    ModelConfig,
    SystemSetting,
    User,
)
from app.modules.maintenance.api.router import MaintenanceOrderInput, order_payload
from app.modules.anomaly_detection.infrastructure.repository import (
    device_anomalies_query,
    excluded_non_operational_analysis_run_ids,
    operational_equipment_anomaly_filter,
)
from app.modules.anomaly_detection.application.device_anomaly_service import (
    grouped_device_summary,
    paged_device_anomalies,
)
from app.modules.devices.application.measurement_severity import measurement_severity

router = APIRouter()
ANOMALY_STATUSES = {
    "new",
    "under_review",
    "confirmed",
    "rejected",
    "reviewed",
    "closed",
}
REVIEWERS = require_roles("administrator", "maintenance_technician")


class ReviewInput(BaseModel):
    status: str
    comments: str | None = Field(default=None, max_length=5000)


FEEDBACK_LABELS = {"confirmed_fault", "false_alarm", "needs_more_data"}
FEEDBACK_ROLES = require_roles("administrator", "maintenance_technician")


class FeedbackInput(BaseModel):
    label: str
    comment: str | None = Field(default=None, max_length=5000)
    maintenance_order_id: int | None = None


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _explanation_payload(anomaly: Anomaly) -> dict:
    analysis = anomaly.analysis_run
    details = (anomaly.expected_range or {}).get("detector_details")
    if not isinstance(details, dict):
        details = analysis.metrics.get("model_result", {}) if analysis else {}
    alert_rules = getattr(anomaly, "alert_rules", None) or ["model"]
    rule_names = {
        "physical_limit": "physical limit",
        "model": "model",
        "flatline": "flatline",
        "persistence": "persistence",
        "stopped_reporting": "stopped reporting",
    }
    observed = anomaly.feature_values or {}
    deviations = []
    stored_deviations = (anomaly.expected_range or {}).get("top_feature_deviations", [])
    baseline_by_feature = details.get("baseline") if isinstance(details.get("baseline"), dict) else {}
    for item in stored_deviations if isinstance(stored_deviations, list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get("feature")
        value = observed.get(name)
        mean = _number(item.get("baseline_mean", (baseline_by_feature.get(name) or {}).get("mean")))
        std = _number(
            item.get("baseline_standard_deviation")
            or (baseline_by_feature.get(name) or {}).get("standard_deviation")
            or details.get("baseline_standard_deviation")
        )
        normalized = _number(item.get("absolute_z_score"))
        if normalized is not None and mean is not None:
            normalized = abs(normalized)
        if normalized is None and _number(value) is not None and mean is not None and std:
            normalized = abs((_number(value) - mean) / std)
        deviations.append({
            "feature": name,
            "value": _number(value),
            "baseline_mean": mean,
            "baseline_standard_deviations": normalized,
            "history_size": getattr(anomaly, "history_size", None),
        })

    if not deviations:
        values = []
        for name, value in observed.items():
            numeric_value = _number(value)
            if numeric_value is None:
                continue
            baseline = baseline_by_feature.get(name) if isinstance(baseline_by_feature, dict) else None
            mean = _number(baseline.get("mean")) if isinstance(baseline, dict) else _number(details.get("baseline_mean"))
            std = _number(baseline.get("standard_deviation")) if isinstance(baseline, dict) else _number(details.get("baseline_standard_deviation"))
            normalized = abs((numeric_value - mean) / std) if mean is not None and std else None
            values.append({
                "feature": name,
                "value": numeric_value,
                "baseline_mean": mean,
                "baseline_standard_deviations": normalized,
                "history_size": getattr(anomaly, "history_size", None),
            })
        deviations = sorted(
            (item for item in values if item["baseline_mean"] is not None and item["baseline_standard_deviations"] is not None),
            key=lambda item: abs(item["baseline_standard_deviations"] or 0),
            reverse=True,
        )[:10]

    configuration = (analysis.metrics or {}).get("configuration", {}) if analysis else {}
    tuned_parameters = configuration.get("tuned_parameters")
    model_config = analysis.model_config if analysis else None
    version = (
        configuration.get("version")
        or configuration.get("model_version")
        or (model_config.parameters.get("version") if model_config and isinstance(model_config.parameters, dict) else None)
        or (str(model_config.id) if model_config else None)
    )
    source_type = None
    if anomaly.equipment_measurement:
        source_type = anomaly.equipment_measurement.source_type
    if source_type is None:
        source_type = analysis.source_type if analysis else None

    ensemble_members = details.get("detectors", []) if isinstance(details, dict) else []
    ensemble = {
        "confidence": details.get("confidence"),
        "voting_rule": details.get("voting") or details.get("voting_rule"),
        "members": ensemble_members if isinstance(ensemble_members, list) else [],
    }
    return {
        "rules_fired": [rule_names.get(rule, rule) for rule in alert_rules],
        "alert_priority": getattr(anomaly, "alert_priority", None),
        "episode_id": getattr(anomaly, "alert_episode_id", None),
        "contributing_parameters": deviations,
        "ensemble": ensemble,
        "model": {
            "name": model_config.name if model_config else configuration.get("name"),
            "version": version,
            "configuration_type": "tuned" if tuned_parameters else "default",
            "configuration_name": (
                tuned_parameters.get("name") if isinstance(tuned_parameters, dict) else None
            ),
            "parameters": model_config.parameters if model_config else configuration.get("parameters", {}),
        },
        "history": {
            "state": getattr(anomaly, "history_state", None),
            "available_readings": getattr(anomaly, "history_size", None),
            "required_readings": getattr(anomaly, "history_required", None),
        },
        "source_type": source_type,
        "evidence_note": "statistical evidence for human review",
    }


def anomaly_payload(anomaly: Anomaly) -> dict:
    analysis = anomaly.analysis_run
    dataset = analysis.dataset if analysis else None
    device = (
        analysis.device
        if analysis and analysis.device and analysis.device.is_registered
        else None
    )
    if device is None and dataset and dataset.device and dataset.device.is_registered:
        device = dataset.device
    equipment_measurement = anomaly.equipment_measurement
    equipment_parameter = (
        equipment_measurement.parameter if equipment_measurement else None
    )
    measurement_source = (
        equipment_measurement.source if equipment_measurement else None
    )
    measurement_source_type = (
        equipment_measurement.source_type if equipment_measurement else None
    )
    configuration = (
        analysis.metrics.get("configuration", {}) if analysis else {}
    )
    run_metrics = analysis.metrics if analysis else {}
    alert_episode = getattr(anomaly, "alert_episode", None)
    pretrained_model = configuration.get("pretrained_model", {})
    parameters = configuration.get("parameters", configuration)
    expected_range = anomaly.expected_range or {}
    return {
        "id": anomaly.id,
        "analysis_run_id": anomaly.analysis_run_id,
        "analysis_status": analysis.status if analysis else None,
        "analysis_created_at": analysis.created_at if analysis else None,
        "dataset_id": dataset.id if dataset else None,
        "dataset_name": dataset.name if dataset else None,
        "device_id": device.id if device else None,
        "device_name": device.name if device else None,
        "device_code": device.device_code if device else None,
        "source_type": analysis.source_type if analysis else None,
        "model_config_id": analysis.model_config_id if analysis else None,
        "model_name": (
            analysis.model_config.name
            if analysis and analysis.model_config
            else pretrained_model.get("name")
        ),
        "algorithm": (
            analysis.model_config.algorithm
            if analysis and analysis.model_config
            else configuration.get("algorithm", anomaly.anomaly_type)
        ),
        "analysis_execution_type": analysis.execution_type if analysis else None,
        "measurement_record_id": anomaly.measurement_record_id,
        "equipment_measurement_id": anomaly.equipment_measurement_id,
        "parameter_id": equipment_parameter.id if equipment_parameter else None,
        "parameter_name": equipment_parameter.name if equipment_parameter else None,
        "measurement_unit": (
            equipment_measurement.unit if equipment_measurement else None
        ),
        "measurement_source": measurement_source,
        "measurement_source_type": measurement_source_type,
        "is_test_data": measurement_source_type == "simulated"
        or measurement_source == "synthetic_test",
        "source_row_number": (
            anomaly.measurement_record.source_row_number
            if anomaly.measurement_record
            else None
        ),
        "measurement_time": (
            anomaly.measurement_record.timestamp
            if anomaly.measurement_record
            else equipment_measurement.measured_at
            if equipment_measurement
            else None
        ),
        "anomaly_type": anomaly.anomaly_type,
        "anomaly_score": anomaly.anomaly_score,
        "severity": anomaly.severity,
        "computed_severity": getattr(anomaly, "computed_severity", None),
        "severity_reason": getattr(anomaly, "severity_reason", None),
        "measurement_severity": (
            getattr(equipment_measurement, "measurement_severity", None)
            if equipment_measurement is not None else None
        ),
        "measurement_severity_reason": (
            getattr(equipment_measurement, "measurement_severity_reason", None)
            if equipment_measurement is not None else None
        ),
        "measurement_detectors": (
            getattr(equipment_measurement, "measurement_detectors", None) or []
            if equipment_measurement is not None else []
        ),
        "measurement_agreeing_detectors": (
            getattr(equipment_measurement, "measurement_agreeing_detectors", None)
            if equipment_measurement is not None else None
        ),
        "measurement_rule_fired": (
            getattr(equipment_measurement, "measurement_rule_fired", None)
            if equipment_measurement is not None else None
        ),
        "measurement_analysis_run_ids": (
            getattr(equipment_measurement, "measurement_analysis_run_ids", None) or []
            if equipment_measurement is not None else []
        ),
        "measurement_deduplicated": equipment_measurement is not None,
        "measurement_severity_rule": (
            "Physical limits first; otherwise 3 eligible detectors plus persistence = High, "
            "2 = Medium, 1 = Low. Statistical severity is never Critical."
        ),
        "status": anomaly.status,
        "alert_episode_id": (
            alert_episode.id
            if alert_episode is not None
            else getattr(anomaly, "alert_episode_id", None)
        ),
        "alert_episode_status": (
            alert_episode.status if alert_episode is not None else None
        ),
        "alert_episode_measurement_ids": (
            alert_episode.measurement_ids or []
            if alert_episode is not None
            else (
                [anomaly.equipment_measurement_id]
                if anomaly.equipment_measurement_id is not None
                else []
            )
        ),
        "alert_rules": getattr(anomaly, "alert_rules", None) or [],
        "alert_priority": (
            alert_episode.priority
            if alert_episode is not None
            else getattr(anomaly, "alert_priority", None)
        ),
        "history_state": (
            getattr(anomaly, "history_state", None)
            or run_metrics.get("alert_evaluation", {}).get("history_state")
        ),
        "history_size": (
            getattr(anomaly, "history_size", None)
            if getattr(anomaly, "history_size", None) is not None
            else run_metrics.get("alert_evaluation", {}).get("history_size")
        ),
        "history_required": (
            getattr(anomaly, "history_required", None)
            if getattr(anomaly, "history_required", None) is not None
            else run_metrics.get("alert_evaluation", {}).get("history_required")
        ),
        "model_result": run_metrics.get("model_result"),
        "alert_evaluation": run_metrics.get("alert_evaluation"),
        "feature_values": anomaly.feature_values,
        "measurement_values": (
            anomaly.measurement_record.values
            if anomaly.measurement_record
            else anomaly.feature_values
            if (
                measurement_source_type == "simulated"
                or measurement_source == "synthetic_test"
            )
            else {
                equipment_parameter.name: equipment_measurement.value,
                "value": equipment_measurement.value,
                "unit": equipment_measurement.unit,
            }
            if equipment_measurement and equipment_parameter
            else anomaly.feature_values
        ),
        "expected_range": expected_range,
        "score_threshold": expected_range.get(
            "score_threshold",
            parameters.get("threshold", parameters.get("z_score_threshold")),
        ),
        "algorithm_parameters": parameters,
        "description": anomaly.description,
        "detected_at": anomaly.detected_at,
        "reviews": [
            {
                "id": review.id,
                "decision": review.decision,
                "comments": review.comments,
                "reviewed_by": review.reviewed_by,
                "reviewed_at": review.reviewed_at,
            }
            for review in sorted(anomaly.reviews, key=lambda item: item.reviewed_at)
        ],
        "maintenance_order_ids": [
            order.id for order in anomaly.maintenance_orders
        ],
        "maintenance_orders": [
            {
                "id": order.id,
                "title": order.title,
                "status": order.status,
                "priority": order.priority,
                "assigned_to": order.assigned_to,
                "assigned_name": (
                    order.assignee.full_name if order.assignee else None
                ),
                "due_date": order.due_date,
                "resolution_notes": order.resolution_notes,
                "created_at": order.created_at,
            }
            for order in anomaly.maintenance_orders
        ],
        "explanation": _explanation_payload(anomaly),
        "feedback": None,
    }


def _anomaly_query(
    *,
    db: Session | None = None,
    source_type: str | None = "equipment",
    include_test_data: bool = False,
    include_dataset: bool = False,
    measurement_source_type: str | None = None,
    status_filter: str | None = None,
    device_id: int | None = None,
    dataset_id: int | None = None,
    analysis_id: int | None = None,
    severity: str | None = None,
    algorithm: str | None = None,
    parameter: str | None = None,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
):
    query = (
        device_anomalies_query(include_dataset=include_dataset)
        .where(Anomaly.archived_at.is_(None))
        .outerjoin(Dataset, Dataset.id == AnalysisRun.dataset_id)
        .outerjoin(
            EquipmentMeasurement,
            EquipmentMeasurement.id == Anomaly.equipment_measurement_id,
        )
        .outerjoin(
            EquipmentParameter,
            EquipmentParameter.id == EquipmentMeasurement.parameter_id,
        )
    )
    if source_type == "equipment":
        equipment_filter = and_(
            *_equipment_anomaly_filters(
                include_test_data=include_test_data,
                db=db,
            )
        )
        if include_dataset:
            query = query.where(
                or_(
                    equipment_filter,
                    and_(
                        AnalysisRun.source_type == "dataset",
                        Anomaly.equipment_measurement_id.is_(None),
                    ),
                )
            )
        else:
            query = query.where(equipment_filter)
    elif source_type == "dataset":
        query = query.where(
            AnalysisRun.source_type == "dataset",
            Anomaly.equipment_measurement_id.is_(None),
        )
    elif source_type == "all":
        query = query.where(
            or_(
                and_(
                    *_equipment_anomaly_filters(
                        include_test_data=True,
                        db=db,
                    )
                ),
                and_(
                    AnalysisRun.source_type == "dataset",
                    Anomaly.equipment_measurement_id.is_(None),
                ),
            )
        )
    elif source_type is not None:
        raise HTTPException(
            status_code=422,
            detail="source_type must be equipment, dataset, or all",
        )
    if measurement_source_type is not None:
        if measurement_source_type not in {"manual", "sensor", "simulated", "dataset"}:
            raise HTTPException(
                status_code=422,
                detail=(
                    "measurement_source_type must be manual, sensor, simulated, "
                    "or dataset"
                ),
            )
        if measurement_source_type == "dataset":
            query = query.where(
                AnalysisRun.source_type == "dataset",
                Anomaly.equipment_measurement_id.is_(None),
            )
        else:
            query = query.where(
                AnalysisRun.source_type == "equipment",
                EquipmentMeasurement.source_type == measurement_source_type,
            )
    if algorithm:
        query = query.where(
            or_(
                AnalysisRun.model_config.has(ModelConfig.algorithm == algorithm),
                AnalysisRun.metrics["configuration"]["algorithm"].as_string()
                == algorithm,
            )
        )
    if status_filter:
        query = query.where(Anomaly.status == status_filter)
    if severity:
        query = query.where(
            Anomaly.severity == severity
        )
    if start_date:
        query = query.where(Anomaly.detected_at >= start_date)
    if end_date:
        query = query.where(Anomaly.detected_at <= end_date)
    if analysis_id:
        query = query.where(Anomaly.analysis_run_id == analysis_id)
    if dataset_id:
        query = query.where(Dataset.id == dataset_id)
    if device_id:
        query = query.where(
            or_(
                AnalysisRun.device_id == device_id,
                Dataset.device_id == device_id,
            )
        )
    if parameter:
        query = query.where(
            EquipmentParameter.name.ilike(f"%{parameter.strip()}%")
        )
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            Dataset.name.ilike(term)
            | AnalysisRun.device.has(Device.name.ilike(term))
            | AnalysisRun.device.has(Device.device_code.ilike(term))
            | Dataset.device.has(Device.name.ilike(term))
            | Dataset.device.has(Device.device_code.ilike(term))
            | EquipmentParameter.name.ilike(term)
            | Anomaly.description.ilike(term)
            | Anomaly.anomaly_type.ilike(term)
            | AnalysisRun.model_config.has(ModelConfig.name.ilike(term))
        )
    return query


def _equipment_anomaly_filters(
    *,
    include_test_data: bool = False,
    db: Session | None = None,
):
    excluded_run_ids = (
        excluded_non_operational_analysis_run_ids(db)
        if db is not None
        else None
    )
    return operational_equipment_anomaly_filter(
        include_test_data=include_test_data,
        excluded_run_ids=excluded_run_ids,
    )


@router.get("")
def list_anomalies(
    source_type: str = "equipment",
    include_test_data: bool = False,
    include_dataset: bool = False,
    measurement_source_type: str | None = None,
    status_filter: str | None = None,
    device_id: int | None = None,
    dataset_id: int | None = None,
    analysis_id: int | None = None,
    severity: str | None = None,
    algorithm: str | None = None,
    parameter: str | None = None,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = _anomaly_query(
        db=db,
        source_type=source_type,
        include_test_data=include_test_data,
        include_dataset=include_dataset,
        measurement_source_type=measurement_source_type,
        status_filter=status_filter,
        device_id=device_id,
        dataset_id=dataset_id,
        analysis_id=analysis_id,
        severity=severity,
        algorithm=algorithm,
        parameter=parameter,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )
    query = query.order_by(Anomaly.detected_at.desc()).limit(limit).offset(offset)
    return [anomaly_payload(anomaly) for anomaly in db.scalars(query)]


@router.get("/summary")
def anomaly_summary(
    source_type: str = "equipment",
    include_test_data: bool = False,
    include_dataset: bool = False,
    measurement_source_type: str | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    source_query = _anomaly_query(
        db=db,
        source_type=source_type,
        include_test_data=include_test_data,
        include_dataset=include_dataset,
        measurement_source_type=measurement_source_type,
    )
    source_ids = source_query.with_only_columns(Anomaly.id).subquery()
    severity_counts = dict(
        db.execute(
            select(Anomaly.severity, func.count(Anomaly.id))
            .where(Anomaly.id.in_(select(source_ids.c.id)))
            .group_by(Anomaly.severity)
        ).all()
    )
    status_counts = dict(
        db.execute(
            select(Anomaly.status, func.count(Anomaly.id))
            .where(Anomaly.id.in_(select(source_ids.c.id)))
            .group_by(Anomaly.status)
        ).all()
    )
    total_detections = db.scalar(
        select(func.count(Anomaly.id)).where(
            Anomaly.id.in_(select(source_ids.c.id))
        )
    ) or 0
    dataset_measurement_count = (
        db.scalar(
            select(func.count(func.distinct(Anomaly.measurement_record_id))).where(
                Anomaly.id.in_(select(source_ids.c.id)),
                Anomaly.measurement_record_id.is_not(None)
            )
        )
        or 0
    )
    equipment_measurement_count = (
        db.scalar(
            select(func.count(func.distinct(Anomaly.equipment_measurement_id))).where(
                Anomaly.id.in_(select(source_ids.c.id)),
                Anomaly.equipment_measurement_id.is_not(None)
            )
        )
        or 0
    )
    unlinked_detections = db.scalar(
        select(func.count(Anomaly.id)).where(
            Anomaly.id.in_(select(source_ids.c.id)),
            Anomaly.measurement_record_id.is_(None),
            Anomaly.equipment_measurement_id.is_(None),
        )
    ) or 0
    unique_affected_measurements = (
        equipment_measurement_count
        if source_type == "equipment"
        else dataset_measurement_count
        if source_type == "dataset"
        else equipment_measurement_count + dataset_measurement_count
    )
    return {
        "total": total_detections,
        "algorithm_specific_detections": total_detections,
        "unique_affected_measurements": unique_affected_measurements,
        "unlinked_detections": unlinked_detections,
        "severity": {
            "high": severity_counts.get("high", 0),
            "medium": severity_counts.get("medium", 0),
            "low": severity_counts.get("low", 0),
            "critical": severity_counts.get("critical", 0),
        },
        "status": status_counts,
        "pending_reviews": status_counts.get("new", 0)
        + status_counts.get("under_review", 0),
        "confirmed": status_counts.get("confirmed", 0),
        "rejected_false_positives": status_counts.get("rejected", 0),
    }


@router.get("/page")
def page_anomalies(
    source_type: str = "equipment",
    include_test_data: bool = False,
    include_dataset: bool = False,
    measurement_source_type: str | None = None,
    status_filter: str | None = None,
    device_id: int | None = None,
    dataset_id: int | None = None,
    analysis_id: int | None = None,
    severity: str | None = None,
    algorithm: str | None = None,
    parameter: str | None = None,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    filters = {
        "db": db,
        "source_type": source_type,
        "include_test_data": include_test_data,
        "include_dataset": include_dataset,
        "measurement_source_type": measurement_source_type,
        "status_filter": status_filter,
        "device_id": device_id,
        "dataset_id": dataset_id,
        "analysis_id": analysis_id,
        "severity": severity,
        "algorithm": algorithm,
        "parameter": parameter,
        "search": search,
        "start_date": start_date,
        "end_date": end_date,
    }
    query = _anomaly_query(**filters)
    if source_type == "equipment" and dataset_id is None:
        raw_anomalies = list(db.scalars(query.order_by(Anomaly.detected_at.desc())))
        grouped = {}
        for anomaly in raw_anomalies:
            if anomaly.equipment_measurement_id is None:
                continue
            grouped.setdefault(anomaly.equipment_measurement_id, []).append(anomaly)
        deduplicated = [
            max(
                measurement_anomalies,
                key=lambda item: (
                    item.detected_at,
                    item.id,
                ),
            )
            for measurement_anomalies in grouped.values()
        ]
        deduplicated.sort(key=lambda item: (item.detected_at, item.id), reverse=True)
        items = []
        for anomaly in deduplicated[offset:offset + limit]:
            payload = anomaly_payload(anomaly)
            projected = measurement_severity(
                grouped[anomaly.equipment_measurement_id]
            )
            if payload["measurement_severity"] is None:
                payload.update({
                    "measurement_severity": projected["severity"],
                    "measurement_severity_reason": projected["rule"]["description"],
                    "measurement_detectors": projected["detectors"],
                    "measurement_agreeing_detectors": projected["agreeing_detectors"],
                    "measurement_rule_fired": projected["rule"]["fired"],
                    "measurement_analysis_run_ids": projected["analysis_run_ids"],
                })
            items.append(payload)
        return {
            "total": len(deduplicated),
            "anomaly_row_total": len(raw_anomalies),
            "limit": limit,
            "offset": offset,
            "deduplicated_by_equipment_measurement": True,
            "items": items,
        }
    count_query = select(func.count()).select_from(
        query.order_by(None).subquery()
    )
    anomalies = list(
        db.scalars(
            query.order_by(Anomaly.detected_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    total = db.scalar(count_query) or 0
    return {
        "total": total,
        "anomaly_row_total": total,
        "limit": limit,
        "offset": offset,
        "items": [anomaly_payload(anomaly) for anomaly in anomalies],
    }


@router.get("/devices/grouped")
def grouped_device_anomalies(
    source_type: str = "equipment",
    include_test_data: bool = True,
    include_dataset: bool = False,
    measurement_source_type: str | None = None,
    status_filter: str | None = None,
    device_id: int | None = None,
    dataset_id: int | None = None,
    analysis_id: int | None = None,
    severity: str | None = None,
    algorithm: str | None = None,
    parameter: str | None = None,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if source_type not in {"equipment", "dataset", "all"}:
        raise HTTPException(
            status_code=422,
            detail="source_type must be equipment, dataset, or all",
        )
    if source_type != "equipment":
        return {"total": 0, "measurement_total": 0, "devices": []}
    return grouped_device_summary(
        db=db,
        anomaly_query=_anomaly_query,
        user=user,
        filters={
            "db": db,
            "source_type": source_type,
            "include_test_data": include_test_data,
            "include_dataset": include_dataset,
            "measurement_source_type": measurement_source_type,
            "status_filter": status_filter,
            "device_id": device_id,
            "dataset_id": dataset_id,
            "analysis_id": analysis_id,
            "severity": severity,
            "algorithm": algorithm,
            "parameter": parameter,
            "search": search,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


@router.get("/devices/{device_id}/page")
def page_device_anomalies(
    device_id: int,
    source_type: str = "equipment",
    include_test_data: bool = True,
    include_dataset: bool = False,
    measurement_source_type: str | None = None,
    status_filter: str | None = None,
    dataset_id: int | None = None,
    analysis_id: int | None = None,
    severity: str | None = None,
    algorithm: str | None = None,
    parameter: str | None = None,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    device = db.get(Device, device_id)
    if device is None or not device.is_registered:
        raise HTTPException(status_code=404, detail="Registered device not found")
    if (
        user.role == "maintenance_technician"
        and device.responsible_user_id != user.id
    ):
        raise HTTPException(
            status_code=403,
            detail="This device is not assigned to you",
        )
    if source_type not in {"equipment", "dataset", "all"}:
        raise HTTPException(
            status_code=422,
            detail="source_type must be equipment, dataset, or all",
        )
    if source_type != "equipment":
        return {"total": 0, "limit": limit, "offset": offset, "items": []}
    return paged_device_anomalies(
        db=db,
        anomaly_query=_anomaly_query,
        device=device,
        user=user,
        filters={
            "db": db,
            "source_type": source_type,
            "include_test_data": include_test_data,
            "include_dataset": include_dataset,
            "measurement_source_type": measurement_source_type,
            "status_filter": status_filter,
            "device_id": device_id,
            "dataset_id": dataset_id,
            "analysis_id": analysis_id,
            "severity": severity,
            "algorithm": algorithm,
            "parameter": parameter,
            "search": search,
            "start_date": start_date,
            "end_date": end_date,
        },
        limit=limit,
        offset=offset,
        anomaly_payload=anomaly_payload,
    )


@router.get("/repeat-cases")
def repeated_anomalies(
    device_id: int | None = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = (
        select(
            Device.id,
            Device.name,
            Device.device_code,
            func.count(Anomaly.id).label("case_count"),
            func.min(Anomaly.detected_at).label("first_detected"),
            func.max(Anomaly.detected_at).label("last_detected"),
        )
        .join(AnalysisRun, AnalysisRun.device_id == Device.id)
        .join(Anomaly, Anomaly.analysis_run_id == AnalysisRun.id)
        .where(Device.is_registered.is_(True))
        .group_by(Device.id, Device.name, Device.device_code)
        .having(func.count(Anomaly.id) > 1)
        .order_by(func.count(Anomaly.id).desc())
    )
    if device_id:
        query = query.where(Device.id == device_id)
    return [
        {
            "device_id": item.id,
            "device_name": item.name,
            "device_code": item.device_code,
            "case_count": item.case_count,
            "first_detected": item.first_detected,
            "last_detected": item.last_detected,
        }
        for item in db.execute(query)
    ]


@router.get("/export.csv")
def export_anomalies(
    source_type: str = "equipment",
    include_test_data: bool = False,
    include_dataset: bool = False,
    measurement_source_type: str | None = None,
    device_id: int | None = None,
    dataset_id: int | None = None,
    status_filter: str | None = None,
    severity: str | None = None,
    algorithm: str | None = None,
    parameter: str | None = None,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "device",
            "dataset",
            "analysis_id",
            "algorithm",
            "measurement_source_type",
            "status",
            "severity",
            "score",
            "detected_at",
            "measurement_values",
        ]
    )
    query = _anomaly_query(
        db=db,
        source_type=source_type,
        include_test_data=include_test_data,
        include_dataset=include_dataset,
        measurement_source_type=measurement_source_type,
        device_id=device_id,
        dataset_id=dataset_id,
        status_filter=status_filter,
        severity=severity,
        algorithm=algorithm,
        parameter=parameter,
        search=search,
        start_date=start_date,
        end_date=end_date,
    ).order_by(Anomaly.detected_at.desc())
    anomalies = list(db.scalars(query))
    for anomaly in anomalies:
        data = anomaly_payload(anomaly)
        writer.writerow(
            [
                data["id"],
                data["device_name"],
                data["dataset_name"],
                data["analysis_run_id"],
                data["algorithm"],
                data["measurement_source_type"]
                or ("dataset" if data["source_type"] == "dataset" else ""),
                data["status"],
                data["severity"],
                data["anomaly_score"],
                data["detected_at"],
                data["measurement_values"],
            ]
        )
    db.add(
        GeneratedReport(
            title="Anomaly measurements export",
            report_type="anomalies",
            file_format="csv",
            parameters={
                "device_id": device_id,
                "dataset_id": dataset_id,
                "source_type": source_type,
                "status": status_filter,
                "severity": severity,
                "algorithm": algorithm,
                "parameter": parameter,
                "search": search,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
            },
            created_by=user.id,
        )
    )
    db.add(
        AuditLog(
            user_id=user.id,
            action="export",
            entity_type="anomaly_report",
            details={"rows": len(anomalies)},
        )
    )
    db.commit()
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="anomalies.csv"'},
    )


def _feedback_payload(feedback: AnomalyFeedback | None) -> dict | None:
    if feedback is None:
        return None
    creator = feedback.creator
    return {
        "id": feedback.id,
        "label": feedback.label,
        "comment": feedback.comment,
        "maintenance_order_id": feedback.maintenance_order_id,
        "created_by": feedback.created_by,
        "created_by_name": creator.full_name if creator else None,
        "created_at": feedback.created_at,
        "updated_at": feedback.updated_at,
        "history": [
            {
                "id": item.id,
                "label": item.label,
                "comment": item.comment,
                "maintenance_order_id": item.maintenance_order_id,
                "changed_by": item.changed_by,
                "changed_by_name": item.changer.full_name if item.changer else None,
                "changed_at": item.changed_at,
            }
            for item in feedback.history
        ],
    }


def _validate_feedback_order(db: Session, order_id: int | None, anomaly: Anomaly | None = None, episode=None) -> None:
    if order_id is None:
        return
    order = db.get(MaintenanceOrder, order_id)
    if order is None:
        raise HTTPException(status_code=422, detail="Maintenance order not found")
    target_device_id = episode.device_id if episode is not None else None
    if target_device_id is None and anomaly is not None:
        target_device_id = anomaly.analysis_run.device_id
        if target_device_id is None and anomaly.analysis_run.dataset:
            target_device_id = anomaly.analysis_run.dataset.device_id
    if order.device_id != target_device_id:
        raise HTTPException(status_code=422, detail="Maintenance order is not linked to the anomaly equipment")


def _feedback_statistics_payload(
    eligible_anomalies: list[Anomaly],
    reviewed_feedback: list[AnomalyFeedback],
    minimum_reviewed: int,
) -> dict:
    anomalies_by_id = {anomaly.id: anomaly for anomaly in eligible_anomalies}
    reviewed = [
        item for item in reviewed_feedback
        if item.anomaly_id in anomalies_by_id
    ]
    counts = {label: 0 for label in sorted(FEEDBACK_LABELS)}
    by_detector: dict[str, int] = {}
    by_device: dict[str, int] = {}
    over_time: dict[str, int] = {}
    for item in reviewed:
        counts[item.label] += 1
        anomaly = anomalies_by_id[item.anomaly_id]
        detector = anomaly.anomaly_type.replace("_", " ")
        by_detector[detector] = by_detector.get(detector, 0) + 1
        device = anomaly.analysis_run.device
        device_name = device.name if device else "Unknown equipment"
        by_device[device_name] = by_device.get(device_name, 0) + 1
        date_key = item.updated_at.date().isoformat()
        over_time[date_key] = over_time.get(date_key, 0) + 1
    reviewed_count = len(reviewed)
    sufficient = reviewed_count >= minimum_reviewed
    share = counts["false_alarm"] / reviewed_count if sufficient and reviewed_count else None
    return {
        "minimum_reviewed": minimum_reviewed,
        "reviewed_anomalies": reviewed_count,
        "counts_by_label": counts,
        "counts_by_detector": by_detector,
        "counts_by_device": by_device,
        "over_time": dict(sorted(over_time.items())),
        "false_alarm_share": round(share, 4) if share is not None else None,
        "false_alarm_percentage": round(share * 100, 2) if share is not None else None,
        "sufficient_cases": sufficient,
        "excluded": {"source_types": ["dataset"]},
        "supervised_model_note": (
            "A supervised model requires more reliably labelled registered-equipment feedback; "
            "none is trained in this view."
        ),
    }


@router.get("/feedback/stats")
def anomaly_feedback_stats(
    minimum_reviewed: int = Query(default=30, ge=1),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    setting = db.get(SystemSetting, "feedback_minimum_reviewed_anomalies")
    if setting is not None:
        minimum_reviewed = max(1, int(setting.value))
    anomalies = list(db.scalars(select(Anomaly)))
    eligible = []
    for anomaly in anomalies:
        source_type = (
            anomaly.equipment_measurement.source_type
            if anomaly.equipment_measurement
            else anomaly.analysis_run.source_type
        )
        device = anomaly.analysis_run.device
        if device is None and anomaly.analysis_run.dataset:
            device = anomaly.analysis_run.dataset.device
        if (
            device is not None
            and device.is_registered
            and source_type in {"manual", "sensor", "simulated"}
            and anomaly.analysis_run.dataset_id is None
        ):
            eligible.append(anomaly)

    feedback = list(db.scalars(select(AnomalyFeedback).where(AnomalyFeedback.anomaly_id.is_not(None))))
    return _feedback_statistics_payload(eligible, feedback, minimum_reviewed)


@router.get("/feedback/export.csv")
def export_anomaly_feedback(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    anomalies = {
        anomaly.id: anomaly
        for anomaly in db.scalars(select(Anomaly))
        if anomaly.analysis_run.dataset_id is None
    }
    feedback = list(db.scalars(select(AnomalyFeedback).where(AnomalyFeedback.anomaly_id.is_not(None))))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "anomaly_id", "label", "comment", "detector", "feature", "value",
        "baseline_mean", "baseline_standard_deviations", "history_size",
        "source_type", "confidence", "voting_rule",
    ])
    for item in feedback:
        anomaly = anomalies.get(item.anomaly_id)
        if anomaly is None:
            continue
        explanation = _explanation_payload(anomaly)
        contributions = explanation["contributing_parameters"]
        ensemble = explanation["ensemble"]
        if not contributions:
            contributions = [{key: None for key in (
                "feature", "value", "baseline_mean", "baseline_standard_deviations", "history_size"
            )}]
        for contribution in contributions:
            writer.writerow([
                anomaly.id, item.label, item.comment or "", anomaly.anomaly_type,
                contribution.get("feature"), contribution.get("value"),
                contribution.get("baseline_mean"), contribution.get("baseline_standard_deviations"),
                contribution.get("history_size"), explanation["source_type"],
                ensemble.get("confidence"), ensemble.get("voting_rule"),
            ])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="anomaly-feedback.csv"'},
    )


@router.post("/{anomaly_id}/feedback")
def set_anomaly_feedback(
    anomaly_id: int,
    payload: FeedbackInput,
    user: User = Depends(FEEDBACK_ROLES),
    db: Session = Depends(get_db),
) -> dict:
    if payload.label not in FEEDBACK_LABELS:
        raise HTTPException(status_code=422, detail="Unsupported feedback label")
    anomaly = db.get(Anomaly, anomaly_id)
    if anomaly is None:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    _validate_feedback_order(db, payload.maintenance_order_id, anomaly=anomaly)
    feedback = db.scalar(select(AnomalyFeedback).where(AnomalyFeedback.anomaly_id == anomaly_id))
    if feedback is None:
        feedback = AnomalyFeedback(
            anomaly_id=anomaly_id,
            label=payload.label,
            comment=payload.comment,
            maintenance_order_id=payload.maintenance_order_id,
            created_by=user.id,
        )
        db.add(feedback)
        db.flush()
    else:
        feedback.label = payload.label
        feedback.comment = payload.comment
        feedback.maintenance_order_id = payload.maintenance_order_id
    db.add(AnomalyFeedbackHistory(
        feedback_id=feedback.id,
        label=payload.label,
        comment=payload.comment,
        maintenance_order_id=payload.maintenance_order_id,
        changed_by=user.id,
    ))
    db.add(AuditLog(
        user_id=user.id,
        action="feedback_update",
        entity_type="anomaly",
        entity_id=anomaly_id,
        details={"label": payload.label, "maintenance_order_id": payload.maintenance_order_id},
    ))
    db.commit()
    db.refresh(feedback)
    return _feedback_payload(feedback)


@router.post("/alert-episodes/{episode_id}/feedback")
def set_episode_feedback(
    episode_id: int,
    payload: FeedbackInput,
    user: User = Depends(FEEDBACK_ROLES),
    db: Session = Depends(get_db),
) -> dict:
    if payload.label not in FEEDBACK_LABELS:
        raise HTTPException(status_code=422, detail="Unsupported feedback label")
    from app.shared.models import AlertEpisode
    episode = db.get(AlertEpisode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Alert episode not found")
    _validate_feedback_order(db, payload.maintenance_order_id, episode=episode)
    feedback = db.scalar(select(AnomalyFeedback).where(AnomalyFeedback.alert_episode_id == episode_id))
    if feedback is None:
        feedback = AnomalyFeedback(
            alert_episode_id=episode_id,
            label=payload.label,
            comment=payload.comment,
            maintenance_order_id=payload.maintenance_order_id,
            created_by=user.id,
        )
        db.add(feedback)
        db.flush()
    else:
        feedback.label = payload.label
        feedback.comment = payload.comment
        feedback.maintenance_order_id = payload.maintenance_order_id
    db.add(AnomalyFeedbackHistory(
        feedback_id=feedback.id,
        label=payload.label,
        comment=payload.comment,
        maintenance_order_id=payload.maintenance_order_id,
        changed_by=user.id,
    ))
    db.add(AuditLog(
        user_id=user.id,
        action="feedback_update",
        entity_type="alert_episode",
        entity_id=episode_id,
        details={"label": payload.label, "maintenance_order_id": payload.maintenance_order_id},
    ))
    db.commit()
    db.refresh(feedback)
    return _feedback_payload(feedback)


@router.get("/{anomaly_id}")
def get_anomaly(
    anomaly_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    anomaly = db.get(Anomaly, anomaly_id)
    if anomaly is None:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    result = anomaly_payload(anomaly)
    feedback = db.scalar(select(AnomalyFeedback).where(AnomalyFeedback.anomaly_id == anomaly_id))
    result["feedback"] = _feedback_payload(feedback)
    equipment_measurement = anomaly.equipment_measurement
    if equipment_measurement:
        earlier = list(
            db.scalars(
                select(EquipmentMeasurement)
                .where(
                    EquipmentMeasurement.parameter_id
                    == equipment_measurement.parameter_id,
                    EquipmentMeasurement.measured_at
                    <= equipment_measurement.measured_at,
                )
                .order_by(EquipmentMeasurement.measured_at.desc())
                .limit(20)
            )
        )
        later = list(
            db.scalars(
                select(EquipmentMeasurement)
                .where(
                    EquipmentMeasurement.parameter_id
                    == equipment_measurement.parameter_id,
                    EquipmentMeasurement.measured_at
                    > equipment_measurement.measured_at,
                )
                .order_by(EquipmentMeasurement.measured_at)
                .limit(20)
            )
        )
        nearby = sorted(
            {item.id: item for item in (*earlier, *later)}.values(),
            key=lambda item: item.measured_at,
        )
        result["trend_feature"] = equipment_measurement.parameter.name
        result["vibration_trend"] = [
            {
                "source_row_number": item.id,
                "chart_label": item.measured_at.isoformat(),
                "timestamp": item.measured_at,
                "value": item.value,
                "is_selected_measurement": item.id == equipment_measurement.id,
            }
            for item in reversed(nearby)
        ]
        return result
    measurement = anomaly.measurement_record
    if measurement is None:
        result["vibration_trend"] = []
        result["trend_feature"] = None
        return result

    candidate_features = [
        name
        for name, value in anomaly.feature_values.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    trend_feature = next(
        (
            name
            for name in candidate_features
            if any(token in name.lower() for token in ("rms", "vibration", "bearing"))
        ),
        candidate_features[0] if candidate_features else None,
    )
    start_row = max(1, measurement.source_row_number - 20)
    end_row = measurement.source_row_number + 20
    records = list(
        db.scalars(
            select(MeasurementRecord)
            .where(
                MeasurementRecord.dataset_id == measurement.dataset_id,
                MeasurementRecord.source_row_number.between(start_row, end_row),
            )
            .order_by(MeasurementRecord.source_row_number)
        )
    )
    result["trend_feature"] = trend_feature
    result["vibration_trend"] = [
        {
            "source_row_number": record.source_row_number,
            "chart_label": (
                record.timestamp.isoformat()
                if record.timestamp
                else str(record.source_row_number)
            ),
            "timestamp": record.timestamp,
            "value": (
                float(record.values[trend_feature])
                if trend_feature
                and isinstance(record.values.get(trend_feature), (int, float))
                else None
            ),
            "is_selected_measurement": record.id == measurement.id,
        }
        for record in records
    ]
    return result


@router.post("/{anomaly_id}/review")
def review_anomaly(
    anomaly_id: int,
    payload: ReviewInput,
    user: User = Depends(REVIEWERS),
    db: Session = Depends(get_db),
) -> dict:
    if payload.status not in ANOMALY_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported anomaly review status")
    anomaly = db.get(Anomaly, anomaly_id)
    if anomaly is None:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    anomaly.status = payload.status
    alert_episode = getattr(anomaly, "alert_episode", None)
    if alert_episode is not None and alert_episode.reviewed_at is None:
        alert_episode.reviewed_at = datetime.now(timezone.utc)
    review = AnomalyReview(
        anomaly_id=anomaly.id,
        reviewed_by=user.id,
        decision=payload.status,
        comments=payload.comments,
    )
    db.add(review)
    db.add(
        AuditLog(
            user_id=user.id,
            action="review",
            entity_type="anomaly",
            entity_id=anomaly.id,
            details={"status": payload.status},
        )
    )
    analysis_owner_id = anomaly.analysis_run.started_by
    if analysis_owner_id is not None and analysis_owner_id != user.id:
        notify_user(
            db,
            analysis_owner_id,
            "Anomaly review completed",
            f"Anomaly #{anomaly.id} was marked {payload.status.replace('_', ' ')}.",
            "anomaly_reviewed",
            "anomaly",
            anomaly.id,
        )
    db.commit()
    db.refresh(anomaly)
    return anomaly_payload(anomaly)


@router.post("/{anomaly_id}/maintenance-order")
def create_maintenance_from_anomaly(
    anomaly_id: int,
    payload: MaintenanceOrderInput,
    user: User = Depends(
        require_roles("administrator", "data_analyst")
    ),
    db: Session = Depends(get_db),
) -> dict:
    anomaly = db.get(Anomaly, anomaly_id)
    if anomaly is None:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    if payload.priority not in {"low", "medium", "high", "critical"}:
        raise HTTPException(status_code=422, detail="Unsupported maintenance priority")
    if payload.assigned_to is not None and user.role != "administrator":
        raise HTTPException(
            status_code=403,
            detail="Only administrators may assign inspection tasks",
        )
    device = anomaly.analysis_run.device
    if device is None and anomaly.analysis_run.dataset:
        device = anomaly.analysis_run.dataset.device
    if device is None or not device.is_registered:
        raise HTTPException(
            status_code=409,
            detail=(
                "This detection belongs to a historical dataset without a "
                "registered equipment device. Review the anomaly, but do not "
                "create a physical maintenance order from experiment data."
            ),
        )
    device_id = device.id
    if payload.assigned_to:
        from app.shared.models import User as Account

        assignee = db.get(Account, payload.assigned_to)
        if assignee is None or not assignee.is_active or assignee.role not in {
            "administrator",
            "maintenance_technician",
        }:
            raise HTTPException(status_code=422, detail="Assignee must be an active technician")
    order = MaintenanceOrder(
        title=payload.title,
        description=payload.description,
        device_id=device_id,
        anomaly_id=anomaly.id,
        assigned_to=payload.assigned_to,
        created_by=user.id,
        priority=payload.priority,
        status="assigned" if payload.assigned_to else "new",
        due_date=payload.due_date,
    )
    db.add(order)
    db.flush()
    db.add(AuditLog(user_id=user.id, action="create", entity_type="maintenance_order", entity_id=order.id))
    test_data = (
        anomaly.equipment_measurement is not None
        and (
            anomaly.equipment_measurement.source_type == "simulated"
            or anomaly.equipment_measurement.source == "synthetic_test"
        )
    )
    notify_user(
        db,
        order.assigned_to,
        "[TEST DATA ONLY] Maintenance order assigned"
        if test_data
        else "Maintenance order assigned",
        (
            f"[Synthetic test fixture, not a physical incident] "
            f"Order #{order.id}: {order.title}"
            if test_data
            else f"Order #{order.id}: {order.title}"
        ),
        "maintenance_assigned",
        "maintenance_order",
        order.id,
        details={"is_test_data": test_data, "anomaly_id": anomaly.id},
        dedupe_key=f"maintenance-assigned:{order.id}",
    )
    db.commit()
    db.refresh(order)
    return order_payload(order)