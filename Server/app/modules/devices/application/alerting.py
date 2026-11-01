from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.models import (
    AlertEpisode,
    AnalysisResult,
    EquipmentMeasurement,
    EquipmentParameter,
    SystemSetting,
)


ALERT_SETTING_DEFAULTS: dict[str, int | float] = {
    "default_consecutive_anomaly_threshold": 3,
    "default_persistence_window_size": 5,
    "default_persistence_anomaly_threshold": 3,
    "alert_episode_close_normal_count": 3,
    "alert_escalation_minutes": 60,
    "manual_extreme_z_score_threshold": 10.0,
    "stale_data_interval_multiplier": 3,
}
FLATLINE_BASELINE_MINIMUM = 30


def evaluate_flatline_values(
    baseline: list[float],
    recent_values: list[float],
    *,
    required_readings: int,
    variance_tolerance: float,
) -> dict[str, int | float | str | bool | None]:
    history_size = len(baseline)
    if history_size < FLATLINE_BASELINE_MINIMUM:
        return {
            "history_state": "insufficient_history",
            "history_size": history_size,
            "history_required": FLATLINE_BASELINE_MINIMUM,
            "baseline_std": None,
            "window_std": None,
            "consecutive_readings": len(recent_values),
            "required_readings": required_readings,
            "variance_tolerance": variance_tolerance,
            "triggered": False,
        }

    baseline_window = np.asarray(
        baseline[-FLATLINE_BASELINE_MINIMUM:], dtype=np.float64
    )
    baseline_std = float(np.std(baseline_window))
    result: dict[str, int | float | str | bool | None] = {
        "history_state": "ready",
        "history_size": history_size,
        "history_required": FLATLINE_BASELINE_MINIMUM,
        "baseline_std": baseline_std,
        "window_std": None,
        "consecutive_readings": len(recent_values),
        "required_readings": required_readings,
        "variance_tolerance": variance_tolerance,
        "triggered": False,
    }
    if baseline_std <= max(1e-12, abs(float(np.mean(baseline_window))) * 1e-12):
        result["history_state"] = "baseline_not_variable"
        return result
    if len(recent_values) < required_readings:
        return result

    window = np.asarray(recent_values[-required_readings:], dtype=np.float64)
    window_std = float(np.std(window))
    result["window_std"] = window_std
    result["triggered"] = window_std <= baseline_std * variance_tolerance
    return result


def alert_setting(db: Session, key: str) -> int | float:
    default = ALERT_SETTING_DEFAULTS[key]
    setting = db.get(SystemSetting, key)
    value = setting.value if setting is not None else default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"System setting {key} must be a number")
    if value <= 0:
        raise ValueError(f"System setting {key} must be positive")
    if isinstance(default, int) and not isinstance(value, int):
        raise ValueError(f"System setting {key} must be an integer")
    return value


@dataclass(frozen=True)
class AlertState:
    history_state: str
    history_size: int
    history_required: int
    consecutive_anomalies: int
    window_anomalies: int
    priority: str | None
    alert_rules: list[str]
    episode: AlertEpisode | None
    episode_created: bool


def evaluate_alert_state(
    db: Session,
    measurement: EquipmentMeasurement,
    *,
    history_size: int,
    history_required: int,
    is_anomalous: bool,
    alert_rules: list[str],
    severity: str | None,
) -> AlertState:
    parameter = measurement.parameter
    history_state = (
        "insufficient_history"
        if history_size < history_required
        else "ready"
    )
    consecutive_threshold = (
        parameter.consecutive_anomaly_threshold
        or int(alert_setting(db, "default_consecutive_anomaly_threshold"))
    )
    window_size = (
        parameter.persistence_window_size
        or int(alert_setting(db, "default_persistence_window_size"))
    )
    persistence_threshold = (
        parameter.persistence_anomaly_threshold
        or int(alert_setting(db, "default_persistence_anomaly_threshold"))
    )
    close_after_normals = int(
        alert_setting(db, "alert_episode_close_normal_count")
    )
    if persistence_threshold > window_size:
        raise ValueError(
            "The persistence anomaly threshold cannot exceed its window size"
        )

    db.scalar(
        select(EquipmentParameter.id)
        .where(EquipmentParameter.id == parameter.id)
        .with_for_update()
    )
    previous = list(
        db.execute(
            select(
                AnalysisResult.is_anomaly,
                EquipmentMeasurement.id,
            )
            .join(
                EquipmentMeasurement,
                EquipmentMeasurement.id == AnalysisResult.equipment_measurement_id,
            )
            .where(
                EquipmentMeasurement.parameter_id == parameter.id,
                EquipmentMeasurement.id != measurement.id,
            )
            .order_by(
                EquipmentMeasurement.measured_at.desc(),
                EquipmentMeasurement.id.desc(),
            )
            .limit(max(0, window_size - 1))
        )
    )
    reading_states = [bool(is_anomalous), *(bool(row[0]) for row in previous)]
    window_anomalies = sum(reading_states)
    consecutive_anomalies = 0
    for state in reading_states:
        if not state:
            break
        consecutive_anomalies += 1
    persistence_triggered = is_anomalous and (
        consecutive_anomalies >= consecutive_threshold
        or window_anomalies >= persistence_threshold
    )
    current_rules = list(dict.fromkeys(alert_rules))
    if persistence_triggered:
        current_rules.append("persistence")

    episode = db.scalar(
        select(AlertEpisode)
        .where(
            AlertEpisode.parameter_id == parameter.id,
            AlertEpisode.status == "open",
        )
        .with_for_update()
    )
    if not is_anomalous:
        if episode is not None:
            episode.normal_readings += 1
            episode.last_measurement_id = measurement.id
            if episode.normal_readings >= close_after_normals:
                episode.status = "closed"
                episode.closed_at = measurement.measured_at
        return AlertState(
            history_state,
            history_size,
            history_required,
            consecutive_anomalies,
            window_anomalies,
            episode.priority if episode else None,
            [],
            episode,
            False,
        )

    priority = "elevated" if persistence_triggered else "watch"
    episode_created = episode is None
    if episode is None:
        episode = AlertEpisode(
            device_id=parameter.device_id,
            parameter_id=parameter.id,
            status="open",
            priority=priority,
            severity=severity or "medium",
            alert_rules=current_rules,
            history_size=history_size,
            history_required=history_required,
            consecutive_anomalies=consecutive_anomalies,
            window_anomalies=window_anomalies,
            first_detected_at=measurement.measured_at,
            last_detected_at=measurement.measured_at,
            last_measurement_id=measurement.id,
            measurement_ids=[measurement.id],
        )
        db.add(episode)
        db.flush()
    else:
        episode.alert_rules = list(
            dict.fromkeys([*(episode.alert_rules or []), *current_rules])
        )
        episode.priority = (
            "elevated"
            if persistence_triggered and episode.priority == "watch"
            else episode.priority
        )
        episode.severity = severity or episode.severity
        episode.history_size = history_size
        episode.history_required = history_required
        episode.consecutive_anomalies = consecutive_anomalies
        episode.window_anomalies = window_anomalies
        episode.normal_readings = 0
        episode.last_detected_at = measurement.measured_at
        episode.last_measurement_id = measurement.id
        measurement_ids = list(episode.measurement_ids or [])
        if measurement.id not in measurement_ids:
            episode.measurement_ids = [*measurement_ids, measurement.id]
    return AlertState(
        history_state,
        history_size,
        history_required,
        consecutive_anomalies,
        window_anomalies,
        episode.priority,
        current_rules,
        episode,
        episode_created,
    )


def manual_extreme_score(
    db: Session,
    parameter: EquipmentParameter,
    value: float,
    measured_at: datetime,
) -> tuple[float | None, float]:
    from app.modules.devices.application.statistics import robust_z_score

    baseline = list(
        db.scalars(
            select(EquipmentMeasurement.value)
            .where(
                EquipmentMeasurement.parameter_id == parameter.id,
                EquipmentMeasurement.measured_at < measured_at,
            )
            .order_by(EquipmentMeasurement.measured_at.desc())
            .limit(500)
        )
    )
    threshold = float(alert_setting(db, "manual_extreme_z_score_threshold"))
    if len(baseline) < 8:
        return None, threshold
    score, _, _ = robust_z_score(value, [float(item) for item in baseline])
    return score, threshold


def escalation_cutoff(db: Session, now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    minutes = int(alert_setting(db, "alert_escalation_minutes"))
    return current - timedelta(minutes=minutes)
