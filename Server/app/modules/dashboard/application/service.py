from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.modules.anomaly_detection.infrastructure.repository import (
    registered_device_anomaly_filter,
)
from app.modules.devices.application.schedule import measurement_schedule
from app.shared.models import (
    AnalysisRun,
    Anomaly,
    AuditLog,
    Dataset,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    MaintenanceOrder,
    MeasurementRecord,
    User,
)

router = APIRouter()
SOURCE_TYPES = ("manual", "sensor", "simulated", "dataset")


def count_rows(db: Session, model: type, *conditions) -> int:
    return db.scalar(
        select(func.count()).select_from(model).where(*conditions)
    ) or 0


def dashboard(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    include_dataset: bool = False,
) -> dict:
    order_status_counts = {
        status: count_rows(db, MaintenanceOrder, MaintenanceOrder.status == status)
        for status in ("new", "assigned", "in_progress", "on_hold", "completed")
    }
    equipment_anomalies = db.execute(
        select(
            Device.id,
            Device.name,
            EquipmentMeasurement.source_type,
            func.count(func.distinct(Anomaly.equipment_measurement_id)),
        )
        .join(AnalysisRun, AnalysisRun.device_id == Device.id)
        .join(Anomaly, Anomaly.analysis_run_id == AnalysisRun.id)
        .join(
            EquipmentMeasurement,
            EquipmentMeasurement.id == Anomaly.equipment_measurement_id,
        )
        .where(
            Device.is_registered.is_(True),
            Device.status != "archived",
            AnalysisRun.source_type == "equipment",
            Anomaly.equipment_measurement_id.is_not(None),
            EquipmentMeasurement.source_type.in_(("manual", "sensor", "simulated")),
        )
        .group_by(Device.id, Device.name, EquipmentMeasurement.source_type)
        .order_by(func.count(func.distinct(Anomaly.equipment_measurement_id)).desc())
    ).all()
    dataset_anomalies = db.execute(
        select(
            Device.id,
            Dataset.id,
            func.coalesce(Device.name, Dataset.name),
            func.count(func.distinct(MeasurementRecord.id)),
        )
        .join(AnalysisRun, AnalysisRun.dataset_id == Dataset.id)
        .join(Anomaly, Anomaly.analysis_run_id == AnalysisRun.id)
        .join(MeasurementRecord, MeasurementRecord.id == Anomaly.measurement_record_id)
        .outerjoin(Device, Device.id == Dataset.device_id)
        .where(
            AnalysisRun.source_type == "dataset",
            Anomaly.equipment_measurement_id.is_(None),
            registered_device_anomaly_filter(include_dataset=include_dataset),
        )
        .group_by(Device.id, Dataset.id, Dataset.name, Device.name)
        .order_by(func.count(func.distinct(MeasurementRecord.id)).desc())
    ).all()
    anomaly_chart_data = [
        {
            "device_id": device_id,
            "device_name": name,
            "source_type": source_type,
            "count": anomaly_count,
        }
        for device_id, name, source_type, anomaly_count in equipment_anomalies
    ] + [
        {
            "device_id": device_id,
            "dataset_id": dataset_id,
            "device_name": name,
            "source_type": "dataset",
            "count": anomaly_count,
        }
        for device_id, dataset_id, name, anomaly_count in dataset_anomalies
    ]
    anomaly_chart_data.sort(key=lambda item: item["count"], reverse=True)
    device_source_counts = dict.fromkeys(SOURCE_TYPES, 0)
    for source_type, count in db.execute(
        select(Device.source_type, func.count(Device.id))
        .where(
            Device.is_registered.is_(True),
            Device.status == "active",
        )
        .group_by(Device.source_type)
    ).all():
        if source_type in device_source_counts:
            device_source_counts[source_type] = count

    completed_analysis_source_counts = dict.fromkeys(SOURCE_TYPES, 0)
    analysis_source = case(
        (AnalysisRun.source_type == "dataset", "dataset"),
        else_=EquipmentMeasurement.source_type,
    )
    for source_type, count in db.execute(
        select(analysis_source, func.count(AnalysisRun.id))
        .outerjoin(
            EquipmentMeasurement,
            EquipmentMeasurement.id == AnalysisRun.equipment_measurement_id,
        )
        .where(
            AnalysisRun.status == "completed",
            or_(
                and_(
                    AnalysisRun.source_type == "dataset",
                    AnalysisRun.dataset_id.is_not(None),
                ),
                and_(
                    AnalysisRun.source_type == "equipment",
                    EquipmentMeasurement.id.is_not(None),
                ),
            ),
        )
        .group_by(analysis_source)
    ).all():
        if source_type in completed_analysis_source_counts:
            completed_analysis_source_counts[source_type] = count

    pending_source_counts = dict.fromkeys(SOURCE_TYPES, 0)
    pending_source = case(
        (AnalysisRun.source_type == "dataset", "dataset"),
        else_=EquipmentMeasurement.source_type,
    )
    for source_type, count in db.execute(
        select(pending_source, func.count(Anomaly.id))
        .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
        .outerjoin(
            EquipmentMeasurement,
            EquipmentMeasurement.id == Anomaly.equipment_measurement_id,
        )
        .outerjoin(
            EquipmentParameter,
            EquipmentParameter.id == EquipmentMeasurement.parameter_id,
        )
        .where(
            Anomaly.status.in_(("new", "under_review")),
            or_(
                and_(
                    AnalysisRun.source_type == "dataset",
                    Anomaly.measurement_record_id.is_not(None),
                ),
                and_(
                    AnalysisRun.source_type == "equipment",
                    AnalysisRun.dataset_id.is_(None),
                    AnalysisRun.equipment_measurement_id
                    == Anomaly.equipment_measurement_id,
                    EquipmentMeasurement.id.is_not(None),
                    Anomaly.measurement_record_id.is_(None),
                    EquipmentMeasurement.source_type.in_(
                        ("manual", "sensor", "simulated")
                    ),
                    EquipmentParameter.device_id == AnalysisRun.device_id,
                ),
            ),
            registered_device_anomaly_filter(include_dataset=include_dataset),
        )
        .group_by(pending_source)
    ).all():
        if source_type in pending_source_counts:
            pending_source_counts[source_type] = count

    enabled_parameters = list(
        db.scalars(
            select(EquipmentParameter)
            .join(Device, EquipmentParameter.device_id == Device.id)
            .where(
                EquipmentParameter.enabled.is_(True),
                Device.is_registered.is_(True),
                Device.status == "active",
                Device.source_type.in_(("manual", "sensor", "simulated")),
            )
        )
    )
    now = datetime.now(timezone.utc)
    overdue_measurements = 0
    for parameter in enabled_parameters:
        latest_at = db.scalar(
            select(EquipmentMeasurement.measured_at)
            .where(
                EquipmentMeasurement.parameter_id == parameter.id,
                EquipmentMeasurement.source_type.in_(("manual", "sensor", "simulated")),
            )
            .order_by(EquipmentMeasurement.measured_at.desc())
            .limit(1)
        )
        _, status = measurement_schedule(parameter, latest_at, now)
        if status == "overdue":
            overdue_measurements += 1
    unreviewed_equipment_anomalies = db.scalar(
        select(func.count(Anomaly.id))
        .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
        .join(
            EquipmentMeasurement,
            EquipmentMeasurement.id == Anomaly.equipment_measurement_id,
        )
        .join(
            EquipmentParameter,
            EquipmentParameter.id == EquipmentMeasurement.parameter_id,
        )
        .join(Device, Device.id == AnalysisRun.device_id)
        .where(
            Anomaly.status.in_(("new", "under_review")),
            Anomaly.measurement_record_id.is_(None),
            Anomaly.equipment_measurement_id.is_not(None),
            AnalysisRun.source_type == "equipment",
            AnalysisRun.dataset_id.is_(None),
            AnalysisRun.equipment_measurement_id
            == Anomaly.equipment_measurement_id,
            EquipmentMeasurement.source_type.in_(("manual", "sensor", "simulated")),
            EquipmentParameter.device_id == AnalysisRun.device_id,
            Device.is_registered.is_(True),
            Device.status != "archived",
        )
    ) or 0
    recent_activity = db.scalars(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8)
    )
    computed_severity_counts = dict.fromkeys(
        ("critical", "high", "medium", "low"), 0
    )
    equipment_severity = func.coalesce(
        EquipmentMeasurement.measurement_severity,
        Anomaly.computed_severity,
        Anomaly.severity,
    )
    for severity, count in db.execute(
        select(equipment_severity, func.count(func.distinct(EquipmentMeasurement.id)))
        .select_from(Anomaly)
        .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
        .join(
            EquipmentMeasurement,
            EquipmentMeasurement.id == Anomaly.equipment_measurement_id,
        )
        .where(
            Anomaly.archived_at.is_(None),
            AnalysisRun.source_type == "equipment",
            EquipmentMeasurement.source_type.in_(("manual", "sensor", "simulated")),
        )
        .group_by(equipment_severity)
    ).all():
        if severity in computed_severity_counts:
            computed_severity_counts[severity] = count
    dataset_severity = func.coalesce(Anomaly.computed_severity, Anomaly.severity)
    for severity, count in db.execute(
        select(dataset_severity, func.count(func.distinct(Anomaly.measurement_record_id)))
        .select_from(Anomaly)
        .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
        .where(
            Anomaly.archived_at.is_(None),
            AnalysisRun.source_type == "dataset",
            Anomaly.measurement_record_id.is_not(None),
        )
        .group_by(dataset_severity)
    ).all():
        if severity in computed_severity_counts:
            computed_severity_counts[severity] += count
    return {
        "role": user.role,
        "metrics": {
            "active_devices": count_rows(
                db, Device, Device.is_registered.is_(True), Device.status == "active"
            ),
            "datasets": count_rows(db, Dataset),
            "completed_analyses": count_rows(
                db, AnalysisRun, AnalysisRun.status == "completed"
            ),
            "failed_analyses": count_rows(
                db, AnalysisRun, AnalysisRun.status == "failed"
            ),
            "anomalies": db.scalar(
                select(func.count(Anomaly.id))
                .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
                .where(
                    registered_device_anomaly_filter(
                        include_dataset=include_dataset
                    )
                )
            ) or 0,
            "equipment_anomalies": count_rows(
                db,
                Anomaly,
                Anomaly.equipment_measurement_id.is_not(None),
                Anomaly.analysis_run.has(
                    AnalysisRun.source_type == "equipment"
                ),
                Anomaly.equipment_measurement.has(
                    EquipmentMeasurement.source_type.in_(("manual", "sensor", "simulated"))
                ),
                Anomaly.analysis_run.has(
                    AnalysisRun.device.has(Device.is_registered.is_(True))
                ),
                Anomaly.analysis_run.has(
                    AnalysisRun.device.has(Device.status != "archived")
                ),
            ),
            "dataset_anomalies": count_rows(
                db,
                Anomaly,
                Anomaly.measurement_record_id.is_not(None),
                Anomaly.equipment_measurement_id.is_(None),
            ),
            "overdue_measurements": overdue_measurements,
            "equipment_measurements": count_rows(
                db,
                EquipmentMeasurement,
                EquipmentMeasurement.source_type.in_(("manual", "sensor", "simulated")),
            ),
            "unreviewed_anomalies": sum(pending_source_counts.values()),
            "unreviewed_equipment_anomalies": unreviewed_equipment_anomalies,
            "open_orders": count_rows(
                db, MaintenanceOrder, MaintenanceOrder.status != "completed"
            ),
        },
        "source_breakdown": {
            "active_devices": device_source_counts,
            "completed_analyses": completed_analysis_source_counts,
            "cases_to_review": pending_source_counts,
        },
        "computed_severity_distribution": computed_severity_counts,
        "maintenance_orders_by_status": order_status_counts,
        "anomalies_by_device": anomaly_chart_data,
        "recent_activity": [
            {
                "id": item.id,
                "user_id": item.user_id,
                "action": item.action,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "created_at": item.created_at,
            }
            for item in recent_activity
        ],
    }
