from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.notifications import notify_roles, notify_user
from app.modules.devices.application.alerting import alert_setting
from app.shared.models import AlertEpisode, AuditLog, EquipmentMeasurement, EquipmentParameter

TELEMETRY_SOURCE_TYPES = {"sensor", "simulated"}
STALE_DATA_RULE = "stale_data"
ALERT_ROLES = {"administrator", "data_analyst", "maintenance_technician"}


def reporting_interval_seconds(parameter: EquipmentParameter) -> int:
    if parameter.expected_interval_seconds is not None:
        return parameter.expected_interval_seconds
    return parameter.expected_interval_minutes * 60


def evaluate_stale_reporting(
    db: Session,
    parameter: EquipmentParameter,
    latest_measurement: EquipmentMeasurement | None,
    now: datetime,
) -> AlertEpisode | None:
    device = parameter.device
    source_type = (
        latest_measurement.source_type
        if latest_measurement is not None
        else device.source_type
    )
    if (
        device.source_type not in TELEMETRY_SOURCE_TYPES
        and source_type not in TELEMETRY_SOURCE_TYPES
    ):
        return None

    multiplier = int(alert_setting(db, "stale_data_interval_multiplier"))
    interval_seconds = reporting_interval_seconds(parameter)
    last_received_at = (
        latest_measurement.received_at
        if latest_measurement is not None
        else parameter.created_at
    )
    if last_received_at.tzinfo is None or last_received_at.utcoffset() is None:
        last_received_at = last_received_at.replace(tzinfo=timezone.utc)
    else:
        last_received_at = last_received_at.astimezone(timezone.utc)
    current_time = (
        now.replace(tzinfo=timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None
        else now.astimezone(timezone.utc)
    )
    stale_after = timedelta(seconds=interval_seconds * multiplier)
    is_stale = current_time - last_received_at >= stale_after

    episode = db.scalar(
        select(AlertEpisode)
        .where(
            AlertEpisode.parameter_id == parameter.id,
            AlertEpisode.status == "open",
        )
        .with_for_update()
    )
    if not is_stale:
        latest_received_at = (
            latest_measurement.received_at
            if latest_measurement is not None
            else None
        )
        if latest_received_at is not None and (
            latest_received_at.tzinfo is None
            or latest_received_at.utcoffset() is None
        ):
            latest_received_at = latest_received_at.replace(tzinfo=timezone.utc)
        if (
            episode is not None
            and latest_measurement is not None
            and latest_received_at > episode.first_detected_at
        ):
            episode.status = "closed"
            episode.closed_at = latest_received_at
            episode.last_measurement_id = latest_measurement.id
            details = {
                "alert_episode_id": episode.id,
                "device_id": device.id,
                "parameter_id": parameter.id,
                "last_reading_at": latest_received_at.isoformat(),
                "source_type": latest_measurement.source_type,
                "alert_rules": episode.alert_rules,
            }
            db.add(
                AuditLog(
                    user_id=None,
                    action="close",
                    entity_type="alert_episode",
                    entity_id=episode.id,
                    details=details,
                )
            )
            notify_roles(
                db,
                ALERT_ROLES,
                "Equipment reporting resumed",
                f"{device.name} · {parameter.name} has resumed reporting.",
                "stale_data_resolved",
                "alert_episode",
                episode.id,
                details=details,
                dedupe_key=f"stale-data:{episode.id}:resolved",
            )
            notify_user(
                db,
                device.responsible_user_id,
                "Equipment reporting resumed",
                f"{device.name} · {parameter.name} has resumed reporting.",
                "stale_data_resolved",
                "alert_episode",
                episode.id,
                details=details,
                dedupe_key=f"stale-data:{episode.id}:resolved",
            )
        return episode

    if episode is not None:
        return episode

    episode = AlertEpisode(
        device_id=device.id,
        parameter_id=parameter.id,
        status="open",
        priority="elevated",
        severity="high",
        alert_rules=[STALE_DATA_RULE],
        measurement_ids=[],
        last_measurement_id=latest_measurement.id if latest_measurement else None,
        history_size=0,
        history_required=0,
        consecutive_anomalies=0,
        window_anomalies=0,
        first_detected_at=current_time,
        last_detected_at=current_time,
    )
    db.add(episode)
    db.flush()
    details = {
        "device": {
            "id": device.id,
            "name": device.name,
            "code": device.device_code,
            "source_type": source_type,
        },
        "detection": {
            "detected_at": current_time.isoformat(),
            "severity": episode.severity,
            "alert_priority": episode.priority,
            "alert_rules": [STALE_DATA_RULE],
        },
        "last_reading_at": last_received_at.isoformat(),
        "expected_interval_seconds": interval_seconds,
        "stale_after_seconds": int(stale_after.total_seconds()),
        "parameter_id": parameter.id,
        "device_id": device.id,
        "alert_episode_id": episode.id,
        "source_type": source_type,
        "wording": "Device stopped reporting; confirm the telemetry connection.",
    }
    notification_prefix = "[SIMULATED] " if source_type == "simulated" else ""
    db.add(
        AuditLog(
            user_id=None,
            action="alert",
            entity_type="alert_episode",
            entity_id=episode.id,
            details=details,
        )
    )
    notify_roles(
        db,
        ALERT_ROLES,
        f"{notification_prefix}Equipment stopped reporting",
        (
            f"{notification_prefix}{device.name} · {parameter.name} stopped reporting. "
            f"Last reading: {last_received_at.isoformat()}."
        ),
        "stale_data",
        "alert_episode",
        episode.id,
        details=details,
        dedupe_key=f"stale-data:{episode.id}:opened",
    )
    notify_user(
        db,
        device.responsible_user_id,
        f"{notification_prefix}Equipment stopped reporting",
        (
            f"{notification_prefix}{device.name} · {parameter.name} stopped reporting. "
            f"Last reading: {last_received_at.isoformat()}."
        ),
        "stale_data",
        "alert_episode",
        episode.id,
        details=details,
        dedupe_key=f"stale-data:{episode.id}:opened",
    )
    return episode
