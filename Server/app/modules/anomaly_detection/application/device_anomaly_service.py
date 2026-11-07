from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.shared.models import (
    AnalysisRun,
    Anomaly,
    Device,
    EquipmentMeasurement,
    User,
)
from app.modules.devices.application.measurement_severity import measurement_severity

AnomalyQueryBuilder = Callable[..., Any]
ANOMALY_SEVERITY_RANK = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}
ANOMALY_SEVERITY_NAMES = {
    0: "unknown",
    1: "low",
    2: "medium",
    3: "high",
    4: "critical",
}


def _filtered_device_query(
    anomaly_query: AnomalyQueryBuilder,
    *,
    filters: dict[str, Any],
):
    return anomaly_query(**filters).where(
        AnalysisRun.source_type == "equipment",
        Anomaly.equipment_measurement_id.is_not(None),
    )


def _measurement_windows(query):
    selected = query.with_only_columns(
        Anomaly.id.label("anomaly_id"),
        AnalysisRun.device_id.label("device_id"),
        Anomaly.equipment_measurement_id.label("measurement_id"),
        EquipmentMeasurement.source_type.label("source_type"),
        EquipmentMeasurement.measured_at.label("measured_at"),
        Anomaly.detected_at.label("anomaly_at"),
        EquipmentMeasurement.measurement_severity.label("measurement_severity"),
        Anomaly.computed_severity.label("computed_severity"),
        Anomaly.severity.label("severity"),
        Anomaly.status.label("status"),
    )
    rows = selected.subquery()
    severity_value = func.coalesce(
        rows.c.measurement_severity,
        rows.c.computed_severity,
        rows.c.severity,
    )
    severity_rank = case(
        (severity_value == "low", ANOMALY_SEVERITY_RANK["low"]),
        (severity_value == "medium", ANOMALY_SEVERITY_RANK["medium"]),
        (severity_value == "high", ANOMALY_SEVERITY_RANK["high"]),
        (severity_value == "critical", ANOMALY_SEVERITY_RANK["critical"]),
        else_=0,
    )
    return (
        select(
            rows.c.device_id,
            rows.c.measurement_id,
            func.count(rows.c.anomaly_id).label("anomaly_count"),
            rows.c.source_type,
            func.max(rows.c.measured_at).label("latest_measurement_at"),
            func.max(rows.c.anomaly_at).label("latest_anomaly_at"),
            func.max(severity_rank).label("severity_rank"),
            func.sum(
                case(
                    (rows.c.status.in_(("new", "under_review")), 1),
                    else_=0,
                )
            ).label("not_reviewed_count"),
        )
        .group_by(rows.c.device_id, rows.c.measurement_id, rows.c.source_type)
        .subquery()
    )


def grouped_device_summary(
    *,
    db: Session,
    anomaly_query: AnomalyQueryBuilder,
    user: User,
    filters: dict[str, Any],
) -> dict:
    filtered = _filtered_device_query(
        anomaly_query,
        filters=filters,
    )
    if user.role == "maintenance_technician":
        filtered = filtered.join(
            Device, Device.id == AnalysisRun.device_id
        ).where(Device.responsible_user_id == user.id)
    windows = _measurement_windows(filtered)
    total = (
        db.scalar(
            select(func.count()).select_from(
                filtered.order_by(None).subquery()
            )
        )
        or 0
    )
    measurement_total = db.scalar(
        select(func.count()).select_from(windows)
    ) or 0
    counts = db.execute(
        select(
            Device.id,
            Device.name,
            Device.device_code,
            func.sum(windows.c.anomaly_count).label("anomaly_count"),
            func.count(windows.c.measurement_id).label("measurement_count"),
            func.max(windows.c.latest_anomaly_at).label("latest_at"),
            func.max(windows.c.severity_rank).label("severity_rank"),
            func.sum(windows.c.not_reviewed_count).label("not_reviewed_count"),
        )
        .join(windows, windows.c.device_id == Device.id)
        .group_by(Device.id, Device.name, Device.device_code)
        .order_by(func.max(windows.c.latest_anomaly_at).desc(), Device.id.asc())
    ).all()
    sources = db.execute(
        select(windows.c.device_id, windows.c.source_type).distinct()
    ).all()
    source_types_by_device: dict[int, set[str]] = {}
    for device_id, source_type in sources:
        source_types_by_device.setdefault(device_id, set()).add(source_type)
    devices = [
        {
            "device_id": row.id,
            "device_name": row.name,
            "device_code": row.device_code,
            "source_types": sorted(source_types_by_device.get(row.id, set())),
            "anomaly_count": row.anomaly_count,
            "measurement_count": row.measurement_count,
            "highest_severity": ANOMALY_SEVERITY_NAMES.get(
                row.severity_rank, "unknown"
            ),
            "latest_at": row.latest_at,
            "not_reviewed_count": row.not_reviewed_count or 0,
        }
        for row in counts
    ]
    return {
        "total": total,
        "measurement_total": measurement_total,
        "devices": devices,
    }


def paged_device_anomalies(
    *,
    db: Session,
    anomaly_query: AnomalyQueryBuilder,
    device: Device | None,
    user: User,
    filters: dict[str, Any],
    limit: int,
    offset: int,
    anomaly_payload: Callable[[Anomaly], dict],
) -> dict:
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
    filtered = _filtered_device_query(
        anomaly_query,
        filters={**filters, "device_id": device.id},
    )
    windows = _measurement_windows(filtered)
    total = db.scalar(select(func.count()).select_from(windows)) or 0
    anomaly_total = (
        db.scalar(
            select(func.count()).select_from(
                filtered.order_by(None).subquery()
            )
        )
        or 0
    )
    page_measurements = db.execute(
        select(windows.c.measurement_id, windows.c.latest_anomaly_at)
        .order_by(
            windows.c.latest_anomaly_at.desc(),
            windows.c.measurement_id.desc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()
    measurement_ids = [row.measurement_id for row in page_measurements]
    if not measurement_ids:
        return {
            "total": total,
            "anomaly_total": anomaly_total,
            "limit": limit,
            "offset": offset,
            "items": [],
        }

    anomalies_by_measurement: dict[int, list[Anomaly]] = {}
    for anomaly in db.scalars(
        filtered.where(
            Anomaly.equipment_measurement_id.in_(measurement_ids)
        )
    ):
        anomalies_by_measurement.setdefault(
            anomaly.equipment_measurement_id, []
        ).append(anomaly)
    items = []
    for measurement_id in measurement_ids:
        measurement_anomalies = anomalies_by_measurement.get(measurement_id, [])
        if not measurement_anomalies:
            continue
        anomaly = max(
            measurement_anomalies,
            key=lambda item: (item.detected_at, item.id),
        )
        payload = anomaly_payload(anomaly)
        projected = measurement_severity(measurement_anomalies)
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
        "total": total,
        "anomaly_total": anomaly_total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }
