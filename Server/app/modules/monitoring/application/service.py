from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
from app.modules.anomaly_detection.infrastructure.repository import (
    registered_device_anomaly_filter,
)
from app.modules.devices.application.schedule import measurement_schedule
from app.shared.models import (
    AnalysisRun,
    AlertEpisode,
    Anomaly,
    AuditLog,
    Dataset,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    MeasurementRecord,
    MaintenanceOrder,
    MonitoringSchedule,
    Notification,
    User,
)

router = APIRouter()
EDITORS = require_roles("administrator", "data_analyst")
ADMIN = require_roles("administrator")


class MonitoringUpdate(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    simulation_enabled: bool | None = None
    simulation_batch_size: int | None = Field(default=None, ge=2, le=10000)


def _schedule(db: Session) -> MonitoringSchedule:
    schedule = db.get(MonitoringSchedule, 1)
    if schedule is None:
        schedule = MonitoringSchedule(
            id=1,
            enabled=True,
            interval_minutes=60,
            simulation_enabled=False,
            simulation_batch_size=100,
            next_run_at=datetime.now(timezone.utc),
        )
        db.add(schedule)
        db.flush()
    return schedule


def _schedule_payload(schedule: MonitoringSchedule) -> dict:
    last_started_at = schedule.last_started_at
    last_completed_at = schedule.last_completed_at
    is_running = last_started_at is not None and (
        last_completed_at is None or last_started_at > last_completed_at
    )
    if is_running:
        execution_status = "running"
    elif schedule.last_error:
        execution_status = "failed"
    elif last_completed_at is not None:
        execution_status = "completed"
    elif not schedule.enabled:
        execution_status = "disabled"
    else:
        execution_status = "idle"
    return {
        "enabled": schedule.enabled,
        "interval_minutes": schedule.interval_minutes,
        "simulation_enabled": schedule.simulation_enabled,
        "simulation_batch_size": schedule.simulation_batch_size,
        "next_run_at": schedule.next_run_at,
        "last_started_at": last_started_at,
        "last_completed_at": last_completed_at,
        "last_error": schedule.last_error,
        "execution_status": execution_status,
    }


def get_monitoring_schedule(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return _schedule_payload(_schedule(db))


def update_monitoring_schedule(
    payload: MonitoringUpdate,
    user: User = Depends(ADMIN),
    db: Session = Depends(get_db),
) -> dict:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=422, detail="Provide at least one monitoring setting to update"
        )
    if any(value is None for value in updates.values()):
        raise HTTPException(
            status_code=422, detail="Monitoring settings cannot be null"
        )
    schedule = _schedule(db)
    for key, value in updates.items():
        setattr(schedule, key, value)
    schedule.updated_by = user.id
    schedule.next_run_at = (
        datetime.now(timezone.utc) if schedule.enabled else None
    )
    db.add(
        AuditLog(
            user_id=user.id,
            action="update",
            entity_type="monitoring_schedule",
            entity_id=schedule.id,
            details=updates,
        )
    )
    db.commit()
    db.refresh(schedule)
    return _schedule_payload(schedule)


def request_monitoring_run(
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    schedule = _schedule(db)
    if not schedule.enabled:
        raise HTTPException(
            status_code=409,
            detail="Enable scheduled monitoring before requesting an analysis run",
        )
    schedule.next_run_at = datetime.now(timezone.utc)
    db.add(
        AuditLog(
            user_id=user.id,
            action="start",
            entity_type="scheduled_monitoring",
            entity_id=schedule.id,
        )
    )
    db.commit()
    return {"status": "queued", "next_run_at": schedule.next_run_at}


def monitoring_dashboard(
    include_dataset: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    devices = list(
        db.scalars(
            select(Device)
            .where(Device.is_registered.is_(True))
            .order_by(Device.name)
        )
    )
    device_health = []
    for device in devices:
        dataset_count = db.scalar(
            select(func.count(Dataset.id)).where(Dataset.device_id == device.id)
        ) or 0
        anomaly_count = db.scalar(
            select(func.count(Anomaly.id))
            .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
            .where(
                AnalysisRun.device_id == device.id,
                AnalysisRun.source_type == "equipment",
                Anomaly.equipment_measurement_id.is_not(None),
                Anomaly.status.in_(("new", "under_review")),
            )
        ) or 0
        last_run = db.scalar(
            select(AnalysisRun)
            .where(
                AnalysisRun.device_id == device.id,
                AnalysisRun.source_type == "equipment",
            )
            .order_by(AnalysisRun.created_at.desc())
            .limit(1)
        )
        parameters = list(
            db.scalars(
                select(EquipmentParameter).where(
                    EquipmentParameter.device_id == device.id,
                )
            )
        )
        latest_measurements = list(
            db.scalars(
                select(EquipmentMeasurement)
                .join(EquipmentParameter)
                .where(EquipmentParameter.device_id == device.id)
                .order_by(
                    EquipmentMeasurement.measured_at.desc(),
                    EquipmentMeasurement.id.desc(),
                )
                .limit(10)
            )
        )
        now = datetime.now(timezone.utc)
        overdue_parameters = []
        measurement_schedule_items = []
        for parameter in parameters:
            latest_at = db.scalar(
                select(EquipmentMeasurement)
                .where(EquipmentMeasurement.parameter_id == parameter.id)
                .order_by(
                    EquipmentMeasurement.received_at.desc(),
                    EquipmentMeasurement.id.desc(),
                )
                .limit(1)
            )
            parameter_run = db.scalar(
                select(AnalysisRun)
                .join(
                    EquipmentMeasurement,
                    EquipmentMeasurement.id
                    == AnalysisRun.equipment_measurement_id,
                )
                .where(EquipmentMeasurement.parameter_id == parameter.id)
                .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
                .limit(1)
            )
            run_metrics = parameter_run.metrics or {} if parameter_run else {}
            alert_evaluation = run_metrics.get("alert_evaluation", {})
            stale_episodes = list(
                db.scalars(
                    select(AlertEpisode)
                    .where(AlertEpisode.parameter_id == parameter.id)
                    .order_by(AlertEpisode.created_at.desc(), AlertEpisode.id.desc())
                    .limit(10)
                )
            )
            stale_episode = next(
                (
                    episode
                    for episode in stale_episodes
                    if "stale_data" in (episode.alert_rules or [])
                ),
                None,
            )
            latest_measurement_at = (
                latest_at.measured_at if latest_at is not None else None
            )
            latest_received_at = (
                latest_at.received_at if latest_at is not None else None
            )
            due_at, schedule_status = measurement_schedule(
                parameter,
                latest_measurement_at,
                now,
            )
            if schedule_status == "overdue":
                overdue_parameters.append(parameter.id)
            measurement_schedule_items.append(
                {
                    "parameter_id": parameter.id,
                    "parameter_name": parameter.name,
                    "enabled": parameter.enabled,
                    "expected_interval_minutes": parameter.expected_interval_minutes,
                    "expected_interval_seconds": parameter.expected_interval_seconds,
                    "last_measurement_at": latest_measurement_at,
                    "last_reading_received_at": latest_received_at,
                    "next_due_at": due_at,
                    "status": schedule_status,
                    "stale_data_status": (
                        "stopped_reporting"
                        if stale_episode is not None
                        and stale_episode.status == "open"
                        else "reporting_resumed"
                        if stale_episode is not None
                        and stale_episode.status == "closed"
                        else "monitoring"
                    ),
                    "stale_data_episode": (
                        {
                            "id": stale_episode.id,
                            "status": stale_episode.status,
                            "priority": stale_episode.priority,
                            "severity": stale_episode.severity,
                            "first_detected_at": stale_episode.first_detected_at,
                            "closed_at": stale_episode.closed_at,
                            "alert_rules": stale_episode.alert_rules or [],
                        }
                        if stale_episode is not None
                        else None
                    ),
                    "alert_rules": alert_evaluation.get("alert_rules", []),
                    "alert_priority": alert_evaluation.get("priority"),
                    "history_state": alert_evaluation.get(
                        "history_state",
                        run_metrics.get("configuration", {})
                        .get("history_evaluation", {})
                        .get("state"),
                    ),
                    "history_size": alert_evaluation.get(
                        "history_size",
                        run_metrics.get("configuration", {})
                        .get("history_evaluation", {})
                        .get("history_size"),
                    ),
                    "history_required": alert_evaluation.get(
                        "history_required",
                        run_metrics.get("configuration", {})
                        .get("history_evaluation", {})
                        .get("history_required"),
                    ),
                }
            )
        if device.status != "active":
            health = device.status
        elif any(
            item["stale_data_status"] == "stopped_reporting"
            for item in measurement_schedule_items
        ):
            health = "stopped_reporting"
        elif anomaly_count:
            health = "attention"
        elif not latest_measurements:
            health = "no_data"
        elif last_run is None or last_run.status in {"pending", "running"}:
            health = "analyzing"
        elif last_run.status == "failed":
            health = "analysis_failed"
        else:
            health = "healthy"
        nasa_dataset = db.scalar(
            select(Dataset)
            .where(
                Dataset.device_id == device.id,
                Dataset.file_format == "nasa_ims",
            )
            .order_by(Dataset.uploaded_at.desc(), Dataset.id.desc())
            .limit(1)
        )
        vibration_trend = None
        if nasa_dataset is not None:
            trend_records = list(
                db.scalars(
                    select(MeasurementRecord)
                    .where(MeasurementRecord.dataset_id == nasa_dataset.id)
                    .order_by(MeasurementRecord.source_row_number.desc())
                    .limit(30)
                )
            )
            if trend_records:
                values = trend_records[0].values
                trend_feature = next(
                    (
                        name
                        for name in ("bearing_1_rms", "sensor_1_rms")
                        if name in values
                    ),
                    None,
                )
                if trend_feature is not None:
                    points = []
                    for record in reversed(trend_records):
                        raw_value = record.values.get(trend_feature)
                        try:
                            value = float(raw_value)
                        except (TypeError, ValueError):
                            continue
                        points.append(
                            {
                                "label": (
                                    record.timestamp.isoformat()
                                    if record.timestamp
                                    else f"Row {record.source_row_number}"
                                ),
                                "source_row_number": record.source_row_number,
                                "value": value,
                            }
                        )
                    vibration_trend = {
                        "feature": trend_feature,
                        "points": points,
                        "data_source": "Historical NASA IMS experiment",
                    }
        measurement_trend = None
        if latest_measurements:
            selected_parameter = latest_measurements[0].parameter
            trend_measurements = list(
                db.scalars(
                    select(EquipmentMeasurement)
                    .where(
                        EquipmentMeasurement.parameter_id
                        == selected_parameter.id
                    )
                    .order_by(EquipmentMeasurement.measured_at.desc())
                    .limit(30)
                )
            )
            measurement_trend = {
                "feature": selected_parameter.name,
                "unit": selected_parameter.unit,
                "data_source": "Manually recorded equipment measurements",
                "points": [
                    {
                        "label": item.measured_at.isoformat(),
                        "source_row_number": item.id,
                        "value": item.value,
                    }
                    for item in reversed(trend_measurements)
                ],
            }
        device_health.append(
            {
                "id": device.id,
                "name": device.name,
                "device_code": device.device_code,
                "device_type": device.device_type,
                "source_type": device.source_type,
                "status": device.status,
                "health": health,
                "dataset_count": dataset_count,
                "open_anomalies": anomaly_count,
                "overdue_measurements": len(overdue_parameters),
                "measurement_schedule": measurement_schedule_items,
                "latest_measurements": [
                    {
                        "id": measurement.id,
                        "parameter_name": measurement.parameter.name,
                        "value": measurement.value,
                        "unit": measurement.unit,
                        "measured_at": measurement.measured_at,
                        "source_type": measurement.source_type,
                    }
                    for measurement in latest_measurements
                ],
                "last_analysis": (
                    {
                        "id": last_run.id,
                        "status": last_run.status,
                        "created_at": last_run.created_at,
                        "anomalies_found": last_run.anomalies_found,
                        "execution_type": last_run.execution_type,
                    }
                    if last_run
                    else None
                ),
                "data_source": (
                    "historical NASA IMS experiment"
                    if nasa_dataset is not None
                    else "manually recorded equipment"
                    if latest_measurements
                    else "no equipment measurements recorded"
                ),
                "vibration_trend": vibration_trend,
                "measurement_trend": measurement_trend,
            }
        )

    runs = list(
        db.scalars(
            select(AnalysisRun)
            .order_by(AnalysisRun.created_at.desc())
            .limit(20)
        )
    )
    anomalies = list(
        db.scalars(
            select(Anomaly)
            .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
            .where(
                registered_device_anomaly_filter(
                    include_dataset=include_dataset
                )
            )
            .order_by(Anomaly.detected_at.desc())
            .limit(20)
        )
    )
    unread_notifications = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.is_read.is_(False),
        )
    ) or 0
    anomaly_notifications = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.notification_type == "anomaly_detected",
            Notification.related_entity_type == "anomaly",
            select(Anomaly.id)
            .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
            .where(
                Anomaly.id == Notification.related_entity_id,
                registered_device_anomaly_filter(
                    include_dataset=include_dataset
                ),
            )
            .exists(),
        )
    ) or 0
    overdue_notifications = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.notification_type == "measurement_overdue",
        )
    ) or 0
    equipment_severity = dict(
        db.execute(
            select(Anomaly.severity, func.count(Anomaly.id))
            .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
            .join(Device, Device.id == AnalysisRun.device_id)
            .where(Anomaly.equipment_measurement_id.is_not(None))
            .where(
                AnalysisRun.source_type == "equipment",
                Device.is_registered.is_(True),
            )
            .group_by(Anomaly.severity)
        ).all()
    )
    dataset_severity = dict(
        db.execute(
            select(Anomaly.severity, func.count(Anomaly.id))
            .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
            .where(
                Anomaly.measurement_record_id.is_not(None),
                Anomaly.equipment_measurement_id.is_(None),
                AnalysisRun.source_type == "dataset",
                registered_device_anomaly_filter(
                    include_dataset=include_dataset
                ),
            )
            .group_by(Anomaly.severity)
        ).all()
    )

    return {
        "severity_distribution": {
            "equipment": equipment_severity,
            "dataset_experiments": dataset_severity,
        },
        "failed_analysis_runs": db.scalar(
            select(func.count(AnalysisRun.id)).where(AnalysisRun.status == "failed")
        ) or 0,
        "pending_maintenance_tasks": db.scalar(
            select(func.count(MaintenanceOrder.id)).where(
                MaintenanceOrder.status != "completed"
            )
        ) or 0,
        "devices": device_health,
        "analysis_history": [
            {
                "id": run.id,
                "dataset_id": run.dataset_id,
                "source_type": run.source_type,
                "equipment_measurement_id": run.equipment_measurement_id,
                "measurement_source_type": (
                    run.equipment_measurement.source_type
                    if run.equipment_measurement
                    else "dataset"
                    if run.source_type == "dataset"
                    else None
                ),
                "dataset_name": run.dataset.name if run.dataset else None,
                "device_name": (
                    run.device.name
                    if run.device
                    else run.dataset.device.name
                    if run.dataset and run.dataset.device
                    else None
                ),
                "model_name": (
                    run.model_config.name if run.model_config else None
                ),
                "status": run.status,
                "execution_type": run.execution_type,
                "source_start_row": run.source_start_row,
                "source_end_row": run.source_end_row,
                "total_records": run.total_records,
                "anomalies_found": run.anomalies_found,
                "error_message": run.error_message,
                "created_at": run.created_at,
                "completed_at": run.completed_at,
            }
            for run in runs
        ],
        "recent_anomalies": [
            {
                "id": anomaly.id,
                "analysis_run_id": anomaly.analysis_run_id,
                "device_name": (
                    anomaly.analysis_run.device.name
                    if anomaly.analysis_run and anomaly.analysis_run.device
                    else anomaly.analysis_run.dataset.device.name
                    if anomaly.analysis_run
                    and anomaly.analysis_run.dataset
                    and anomaly.analysis_run.dataset.device
                    else None
                ),
                "dataset_name": (
                    anomaly.analysis_run.dataset.name
                    if anomaly.analysis_run and anomaly.analysis_run.dataset
                    else None
                ),
                "severity": anomaly.severity,
                "status": anomaly.status,
                "anomaly_type": anomaly.anomaly_type,
                "anomaly_score": anomaly.anomaly_score,
                "measurement_time": (
                    anomaly.measurement_record.timestamp
                    if anomaly.measurement_record
                    else anomaly.equipment_measurement.measured_at
                    if anomaly.equipment_measurement
                    else None
                ),
                "source_type": (
                    anomaly.analysis_run.source_type
                    if anomaly.analysis_run
                    else None
                ),
                "measurement_source_type": (
                    anomaly.equipment_measurement.source_type
                    if anomaly.equipment_measurement
                    else "dataset"
                    if anomaly.measurement_record is not None
                    else None
                ),
                "parameter_name": (
                    anomaly.equipment_measurement.parameter.name
                    if anomaly.equipment_measurement
                    else None
                ),
                "detected_at": anomaly.detected_at,
                "feature_values": anomaly.feature_values,
                "description": anomaly.description,
                "alert_rules": anomaly.alert_rules or [],
                "alert_priority": (
                    anomaly.alert_episode.priority
                    if anomaly.alert_episode
                    else anomaly.alert_priority
                ),
                "history_state": (
                    anomaly.history_state
                    or (
                        (anomaly.analysis_run.metrics or {})
                        .get("alert_evaluation", {})
                        .get("history_state")
                        if anomaly.analysis_run
                        else None
                    )
                ),
                "history_size": (
                    anomaly.history_size
                    if anomaly.history_size is not None
                    else (
                        (anomaly.analysis_run.metrics or {})
                        .get("alert_evaluation", {})
                        .get("history_size")
                        if anomaly.analysis_run
                        else None
                    )
                ),
                "history_required": (
                    anomaly.history_required
                    if anomaly.history_required is not None
                    else (
                        (anomaly.analysis_run.metrics or {})
                        .get("alert_evaluation", {})
                        .get("history_required")
                        if anomaly.analysis_run
                        else None
                    )
                ),
            }
            for anomaly in anomalies
        ],
        "notification_status": {
            "unread": unread_notifications,
            "anomaly_notifications": anomaly_notifications,
            "overdue_measurement_notifications": overdue_notifications,
        },
    }
