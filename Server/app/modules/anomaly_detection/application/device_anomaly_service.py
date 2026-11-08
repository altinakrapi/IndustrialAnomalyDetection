from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import Integer, case, cast, func, select, text
from sqlalchemy.orm import Session

from app.shared.models import (
    AnalysisRun,
    Anomaly,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
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
BUCKET_MINUTE_OPTIONS = {1, 5, 10, 15, 30, 60}


def bucket_start_for_time(
    detected_at: datetime,
    timezone_name: str,
    bucket_minutes: int,
) -> datetime:
    zone = ZoneInfo(timezone_name)
    if detected_at.tzinfo is None:
        detected_at = detected_at.replace(tzinfo=timezone.utc)
    local_time = detected_at.astimezone(zone)
    minute = (local_time.minute // bucket_minutes) * bucket_minutes
    local_start = local_time.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )
    return local_start.astimezone(timezone.utc)


def sqlite_bucket_start(
    detected_at: str | datetime,
    timezone_name: str,
    bucket_minutes: int,
) -> str:
    value = (
        detected_at
        if isinstance(detected_at, datetime)
        else datetime.fromisoformat(detected_at)
    )
    result = bucket_start_for_time(
        value,
        timezone_name,
        bucket_minutes,
    )
    return result.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def _bucket_start_expression(
    db: Session,
    detected_at,
    timezone_name: str,
    bucket_minutes: int,
):
    if db.get_bind().dialect.name == "sqlite":
        return func.anomaly_bucket_utc(
            detected_at,
            timezone_name,
            bucket_minutes,
        )

    local_time = func.timezone(timezone_name, detected_at)
    local_minute = func.floor(func.extract("minute", local_time))
    local_bucket = func.date_trunc("minute", local_time) - (
        func.mod(local_minute, bucket_minutes)
        * text("INTERVAL '1 minute'")
    )
    utc_offset_seconds = func.extract(
        "epoch",
        local_time - func.timezone("UTC", detected_at),
    )
    return func.timezone("UTC", local_bucket) - (
        utc_offset_seconds * text("INTERVAL '1 second'")
    )


def _validate_bucket_arguments(timezone_name: str, bucket_minutes: int) -> None:
    validate_bucket_minutes(bucket_minutes)
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise HTTPException(
            status_code=422,
            detail="timezone must be a valid IANA timezone",
        ) from error


def validate_bucket_minutes(bucket_minutes: int) -> None:
    if bucket_minutes not in BUCKET_MINUTE_OPTIONS:
        raise HTTPException(
            status_code=422,
            detail="bucket_minutes must be one of 1, 5, 10, 15, 30, or 60",
        )


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
            cast(
                func.sum(windows.c.anomaly_count),
                Integer,
            ).label("anomaly_count"),
            func.count(windows.c.measurement_id).label("measurement_count"),
            func.max(windows.c.latest_anomaly_at).label("latest_at"),
            func.max(windows.c.severity_rank).label("severity_rank"),
            cast(
                func.sum(windows.c.not_reviewed_count),
                Integer,
            ).label("not_reviewed_count"),
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
            "anomaly_count": int(row.anomaly_count),
            "measurement_count": int(row.measurement_count),
            "highest_severity": ANOMALY_SEVERITY_NAMES.get(
                row.severity_rank, "unknown"
            ),
            "latest_at": row.latest_at,
            "not_reviewed_count": int(row.not_reviewed_count or 0),
        }
        for row in counts
    ]
    return {
        "total": total,
        "measurement_total": measurement_total,
        "devices": devices,
    }


def paged_device_anomaly_buckets(
    *,
    db: Session,
    anomaly_query: AnomalyQueryBuilder,
    device: Device | None,
    user: User,
    filters: dict[str, Any],
    timezone_name: str,
    bucket_minutes: int,
    limit: int,
    offset: int,
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
    _validate_bucket_arguments(timezone_name, bucket_minutes)
    filtered = _filtered_device_query(
        anomaly_query,
        filters={**filters, "device_id": device.id},
    )
    rows = filtered.with_only_columns(
        Anomaly.id.label("anomaly_id"),
        Anomaly.detected_at.label("detected_at"),
        Anomaly.status.label("status"),
        Anomaly.anomaly_type.label("detector"),
        Anomaly.computed_severity.label("computed_severity"),
        Anomaly.severity.label("severity"),
        EquipmentMeasurement.value.label("value"),
        EquipmentMeasurement.unit.label("unit"),
        EquipmentMeasurement.measurement_severity.label("measurement_severity"),
        EquipmentParameter.id.label("parameter_id"),
        EquipmentParameter.name.label("parameter_name"),
    ).order_by(None).subquery()
    bucket_start = _bucket_start_expression(
        db,
        rows.c.detected_at,
        timezone_name,
        bucket_minutes,
    ).label("bucket_start")
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
    if db.get_bind().dialect.name == "postgresql":
        detectors = func.array_agg(func.distinct(rows.c.detector))
    else:
        detectors = func.group_concat(func.distinct(rows.c.detector))
    grouped = (
        select(
            bucket_start,
            rows.c.parameter_id,
            rows.c.parameter_name,
            func.count(rows.c.anomaly_id).label("anomaly_count"),
            func.min(rows.c.value).label("minimum_value"),
            func.max(rows.c.value).label("maximum_value"),
            func.min(rows.c.unit).label("unit"),
            func.max(severity_rank).label("severity_rank"),
            cast(
                func.sum(
                    case(
                        (rows.c.status.in_(("new", "under_review")), 1),
                        else_=0,
                    )
                ),
                Integer,
            ).label("not_reviewed_count"),
            func.count(func.distinct(rows.c.status)).label("status_count"),
            func.min(rows.c.status).label("single_status"),
            detectors.label("detectors"),
        )
        .select_from(rows)
        .group_by(bucket_start, rows.c.parameter_id, rows.c.parameter_name)
    )
    bucket_query = grouped.subquery()
    bucket_count = int(
        db.scalar(select(func.count()).select_from(bucket_query)) or 0
    )
    anomaly_total = int(
        db.scalar(
            select(func.count()).select_from(
                filtered.order_by(None).subquery()
            )
        )
        or 0
    )
    page = db.execute(
        select(bucket_query)
        .order_by(
            bucket_query.c.bucket_start.desc(),
            bucket_query.c.parameter_id.asc(),
        )
        .offset(offset)
        .limit(limit)
    ).mappings()
    items = []
    for row in page:
        start = row["bucket_start"]
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        raw_detectors = row["detectors"]
        detector_names = (
            sorted(filter(None, raw_detectors.split(",")))
            if isinstance(raw_detectors, str)
            else sorted(raw_detectors or [])
        )
        items.append({
            "bucket_start": start.astimezone(timezone.utc),
            "parameter_id": row["parameter_id"],
            "parameter_name": row["parameter_name"] or "Equipment parameter",
            "anomaly_count": int(row["anomaly_count"]),
            "minimum_value": row["minimum_value"],
            "maximum_value": row["maximum_value"],
            "unit": row["unit"],
            "highest_severity": ANOMALY_SEVERITY_NAMES.get(
                row["severity_rank"], "unknown"
            ),
            "detectors": detector_names,
            "not_reviewed_count": int(row["not_reviewed_count"] or 0),
            "review_status": (
                "mixed"
                if row["status_count"] > 1
                else row["single_status"]
            ),
        })
    return {
        "total": bucket_count,
        "anomaly_total": anomaly_total,
        "limit": limit,
        "offset": offset,
        "bucket_minutes": bucket_minutes,
        "timezone": timezone_name,
        "items": items,
    }


def paged_anomalies_in_bucket(
    *,
    db: Session,
    anomaly_query: AnomalyQueryBuilder,
    device: Device | None,
    user: User,
    filters: dict[str, Any],
    parameter_id: int,
    bucket_start: datetime,
    timezone_name: str,
    bucket_minutes: int,
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
    _validate_bucket_arguments(timezone_name, bucket_minutes)
    filtered = _filtered_device_query(
        anomaly_query,
        filters={**filters, "device_id": device.id},
    )
    bucket_expression = _bucket_start_expression(
        db,
        Anomaly.detected_at,
        timezone_name,
        bucket_minutes,
    )
    query = filtered.where(
        EquipmentMeasurement.parameter_id == parameter_id,
        bucket_expression == bucket_start.astimezone(timezone.utc),
    )
    total = int(
        db.scalar(
            select(func.count()).select_from(
                query.order_by(None).subquery()
            )
        )
        or 0
    )
    anomalies = db.scalars(
        query.order_by(Anomaly.detected_at.desc(), Anomaly.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [anomaly_payload(anomaly) for anomaly in anomalies],
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
