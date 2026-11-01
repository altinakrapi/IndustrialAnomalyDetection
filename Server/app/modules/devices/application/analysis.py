import time
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.notifications import notify_roles, notify_user
from app.modules.analyses.domain.detectors import (
    DETECTOR_REGISTRY,
    EnsembleDetector,
)
from app.modules.devices.application.alerting import (
    evaluate_alert_state,
    evaluate_flatline_values,
)
from app.modules.devices.application.statistics import robust_z_score as _robust_z_score
from app.modules.devices.application.severity import percentile_severity
from app.modules.datasets.application.nasa_ims_model import (
    FEATURE_NAMES,
    is_compatible_nasa_ims_observation,
    model_is_available,
    predict_nasa_ims_matrix,
)
from app.shared.models import (
    AnalysisResult,
    AnalysisRun,
    Anomaly,
    AuditLog,
    EquipmentMeasurement,
    EquipmentParameter,
    User,
)

_BASELINE_LIMIT = 500
_Z_SCORE_MINIMUM = 8
_ISOLATION_FOREST_MINIMUM = 20


def _compatible_nasa_ims_features(
    db: Session, measurement: EquipmentMeasurement
) -> dict[str, float] | None:
    device = measurement.parameter.device
    if not device.is_registered or measurement.source_type not in {
        "manual", "sensor", "simulated"
    }:
        return None
    compatible_sources = (
        ("simulated",)
        if measurement.source_type == "simulated"
        else ("manual", "sensor")
    )

    features = dict(
        db.execute(
            select(EquipmentParameter.name, EquipmentMeasurement.value)
            .join(
                EquipmentMeasurement,
                EquipmentMeasurement.parameter_id == EquipmentParameter.id,
            )
            .where(
                EquipmentParameter.device_id == device.id,
                EquipmentParameter.name.in_(FEATURE_NAMES),
                EquipmentMeasurement.measured_at == measurement.measured_at,
                EquipmentMeasurement.source_type.in_(compatible_sources),
            )
        ).all()
    )
    if not is_compatible_nasa_ims_observation(features):
        return None
    return {name: float(features[name]) for name in FEATURE_NAMES}


def _outside(value: float, low: float | None, high: float | None) -> bool:
    return (low is not None and value < low) or (
        high is not None and value > high
    )


def analyze_equipment_measurement(
    db: Session, measurement: EquipmentMeasurement
) -> AnalysisRun:
    parameter = measurement.parameter
    device = parameter.device
    config = {
        "algorithm": parameter.analysis_method,
        "parameter_id": parameter.id,
        "parameter": parameter.name,
        "unit": measurement.unit,
        "lower_limit": parameter.lower_limit,
        "upper_limit": parameter.upper_limit,
        "warning_low": parameter.warning_low,
        "warning_high": parameter.warning_high,
        "critical_low": parameter.critical_low,
        "critical_high": parameter.critical_high,
        "z_score_threshold": parameter.z_score_threshold,
        "isolation_contamination": parameter.isolation_contamination,
        "expected_interval_minutes": parameter.expected_interval_minutes,
        "expected_interval_seconds": getattr(
            parameter, "expected_interval_seconds", None
        ),
    }
    execution_key = f"equipment:{measurement.id}:{parameter.updated_at.isoformat()}"
    started_at = datetime.now(timezone.utc)
    started_clock = time.perf_counter()
    run = AnalysisRun(
        dataset_id=None,
        device_id=device.id,
        equipment_measurement_id=measurement.id,
        model_config_id=None,
        started_by=measurement.recorded_by,
        status="running",
        execution_type="measurement",
        source_type="equipment",
        execution_key=execution_key,
        total_records=0,
        anomalies_found=0,
        metrics={"configuration": config},
        started_at=started_at,
    )
    db.add(run)
    db.flush()

    history = list(
        db.scalars(
            select(EquipmentMeasurement.value)
            .where(
                EquipmentMeasurement.parameter_id == parameter.id,
                EquipmentMeasurement.measured_at < measurement.measured_at,
                EquipmentMeasurement.source_type.in_(
                    ("simulated",)
                    if measurement.source_type == "simulated"
                    else ("manual", "sensor")
                ),
            )
            .order_by(EquipmentMeasurement.measured_at.desc())
            .limit(_BASELINE_LIMIT)
        )
    )
    baseline = [float(item) for item in reversed(history)]
    value = float(measurement.value)
    method = parameter.analysis_method
    flatline_reading_count = getattr(parameter, "flatline_reading_count", 10)
    flatline_result = evaluate_flatline_values(
        baseline,
        [*baseline[-(flatline_reading_count - 1):], value],
        required_readings=flatline_reading_count,
        variance_tolerance=getattr(
            parameter, "flatline_variance_tolerance", 0.05
        ),
    )
    flatline_triggered = bool(flatline_result["triggered"])
    anomaly_type: str | None = None
    severity: str | None = None
    score: float | None = None
    score_threshold: float | None = None
    model_anomalous = False
    reference: dict[str, float | None] = {
        "lower_limit": parameter.lower_limit,
        "upper_limit": parameter.upper_limit,
        "warning_low": parameter.warning_low,
        "warning_high": parameter.warning_high,
        "critical_low": parameter.critical_low,
        "critical_high": parameter.critical_high,
    }
    explanation = ""

    limit_severity: str | None = None
    if _outside(value, parameter.critical_low, parameter.critical_high):
        anomaly_type = "threshold"
        severity = "critical"
        limit_severity = "critical"
        explanation = "The measurement crossed a configured critical threshold."
    elif _outside(value, parameter.warning_low, parameter.warning_high):
        anomaly_type = "threshold"
        severity = "high"
        limit_severity = "high"
        explanation = "The measurement crossed a configured warning threshold."
    elif _outside(value, parameter.lower_limit, parameter.upper_limit):
        anomaly_type = "threshold"
        severity = "medium"
        limit_severity = "medium"
        explanation = "The measurement is outside the configured acceptable range."

    baseline_note: str | None = None
    nasa_features: dict[str, float] | None = None
    history_required = (
        _Z_SCORE_MINIMUM
        if method == "z_score"
        else _ISOLATION_FOREST_MINIMUM
        if method == "isolation_forest"
        else max(
            DETECTOR_REGISTRY["isolation_forest"].minimum_history({}),
            DETECTOR_REGISTRY["z_score"].minimum_history(
                {"threshold": parameter.z_score_threshold}
            ),
        )
        if method == "ensemble"
        else 0
    )
    model_result: dict[str, object] = {
        "algorithm": method,
        "evaluated": False,
        "is_anomaly": False,
        "score": None,
        "score_threshold": None,
    }
    if limit_severity is not None:
        model_result["skipped_reason"] = "physical_limit_alert"
    elif method == "z_score":
        if len(baseline) >= _Z_SCORE_MINIMUM:
            score, center, scale = _robust_z_score(value, baseline)
            if score is not None:
                score_threshold = parameter.z_score_threshold
                reference.update({"baseline_center": center, "baseline_scale": scale})
                model_anomalous = score > score_threshold
                model_result = {
                    "algorithm": method,
                    "evaluated": True,
                    "is_anomaly": model_anomalous,
                    "score": score,
                    "score_threshold": score_threshold,
                }
                if model_anomalous and limit_severity is None:
                    anomaly_type = "z_score"
                    severity = "medium" if score <= score_threshold * 1.5 else "high"
                    explanation = (
                        f"Robust Z-score {score:.3f} exceeded the configured "
                        f"threshold {score_threshold:.3f} using {len(baseline)} "
                        "earlier measurements."
                    )
        else:
            baseline_note = (
                f"Z-score was not evaluated: {len(baseline)} earlier measurements "
                f"are available; at least {_Z_SCORE_MINIMUM} are required."
            )
    elif method == "isolation_forest":
        nasa_features = _compatible_nasa_ims_features(db, measurement)
        if len(baseline) >= _ISOLATION_FOREST_MINIMUM:
            if nasa_features is not None:
                if not model_is_available():
                    raise FileNotFoundError(
                        "A complete NASA IMS-compatible equipment observation was "
                        "recorded, but the trained NASA IMS model artifact is unavailable."
                    )
                model_matrix = np.asarray(
                    [[nasa_features[name] for name in FEATURE_NAMES]],
                    dtype=np.float64,
                )
                predictions, scores, score_threshold, model_metadata = (
                    predict_nasa_ims_matrix(model_matrix)
                )
                score = float(scores[0])
                reference.update(
                    {
                        "nasa_ims_feature_count": float(len(FEATURE_NAMES)),
                        "model_score_threshold": score_threshold,
                    }
                )
                config["pretrained_model"] = {
                    "name": model_metadata["name"],
                    "model_version": model_metadata["model_version"],
                    "training_dataset_id": model_metadata["training_dataset_id"],
                    "training_dataset_name": model_metadata["training_dataset_name"],
                    "preprocessing": model_metadata["preprocessing"],
                }
                model_anomalous = int(predictions[0]) == -1
                model_result = {
                    "algorithm": "nasa_ims_isolation_forest",
                    "evaluated": True,
                    "is_anomaly": model_anomalous,
                    "score": score,
                    "score_threshold": score_threshold,
                }
                if model_anomalous and limit_severity is None:
                    anomaly_type = "isolation_forest"
                    severity = "high"
                    explanation = (
                        "The trained NASA IMS Isolation Forest identified this "
                        "measurement-time vibration feature vector as unusual. "
                        "All 36 required bearing features were present and passed "
                        "through the model's fitted imputer and scaler."
                    )
            else:
                from sklearn.ensemble import IsolationForest

                training = np.asarray(baseline, dtype=float).reshape(-1, 1)
                estimator = IsolationForest(
                    contamination=parameter.isolation_contamination,
                    random_state=42,
                    n_estimators=100,
                )
                estimator.fit(training)
                score = float(-estimator.score_samples(np.asarray([[value]]))[0])
                score_threshold = float(-estimator.offset_)
                reference.update(
                    {
                        "baseline_min": float(np.min(training)),
                        "baseline_max": float(np.max(training)),
                        "baseline_count": float(len(baseline)),
                    }
                )
                model_anomalous = int(
                    estimator.predict(np.asarray([[value]]))[0]
                ) == -1
                model_result = {
                    "algorithm": "isolation_forest",
                    "evaluated": True,
                    "is_anomaly": model_anomalous,
                    "score": score,
                    "score_threshold": score_threshold,
                }
                if model_anomalous and limit_severity is None:
                    anomaly_type = "isolation_forest"
                    severity = "high"
                    explanation = (
                        f"Isolation Forest identified the value as unusual relative to "
                        f"{len(baseline)} earlier measurements for this parameter."
                    )
                baseline_note = (
                    "The trained NASA IMS model was not used because this equipment "
                    "reading did not include all 36 compatible bearing features at "
                    "the same measurement timestamp; this result uses only this "
                    "parameter's own historical baseline."
                )
        else:
            prefix = (
                "The trained NASA IMS model was not used because this equipment "
                "reading did not include all 36 compatible bearing features at "
                "the same measurement timestamp. "
                if nasa_features is None
                else ""
            )
            baseline_note = (
                f"{prefix}Isolation Forest was not evaluated: {len(baseline)} "
                f"earlier measurements are available; at least "
                f"{_ISOLATION_FOREST_MINIMUM} are required."
            )
            model_result["history_state"] = "insufficient_history"
    elif method == "ensemble":
        detector = EnsembleDetector()
        ensemble_parameters = detector.validate_config({
            "members": [
                {
                    "algorithm": "isolation_forest",
                    "parameters": {
                        "contamination": parameter.isolation_contamination,
                        "random_state": 42,
                    },
                    "features": [0],
                    "weight": 1.0,
                },
                {
                    "algorithm": "z_score",
                    "parameters": {"threshold": parameter.z_score_threshold},
                    "features": [0],
                    "weight": 1.0,
                },
            ],
            "voting_mode": "majority",
            "quorum": 2,
            "high_confidence_threshold": 0.75,
        })
        history_matrix = np.asarray(baseline, dtype=np.float64).reshape(-1, 1)
        fitted = detector.fit(
            history_matrix,
            parameters=ensemble_parameters,
            missing_values_strategy="median",
            scaling="standard",
        )
        current_value = np.asarray([[value]], dtype=np.float64)
        result_details = detector.result_details(fitted, current_value)[0]
        score = float(detector.score(fitted, current_value)[0])
        model_anomalous = int(detector.predict(fitted, current_value)[0]) == -1
        is_ready = result_details["ensemble_state"] == "ready"
        model_result = {
            "algorithm": method,
            "algorithm_version": detector.version(),
            "evaluated": is_ready,
            "is_anomaly": model_anomalous,
            "score": score,
            "score_threshold": None,
            **result_details,
        }
        config["ensemble_configuration"] = result_details["configuration"]
        if not is_ready:
            baseline_note = (
                "The ensemble did not vote because fewer than its configured "
                "quorum of detectors had enough earlier history."
            )
        elif model_anomalous:
            anomaly_type = "ensemble"
            severity = "high" if result_details["high_confidence"] else "medium"
            reference.update({
                "ensemble_confidence": result_details["confidence"],
                "high_confidence_threshold": (
                    result_details["configuration"]["high_confidence_threshold"]
                ),
            })
            explanation = (
                f"The detector ensemble flagged this measurement with "
                f"{result_details['agreement_count']} of "
                f"{result_details['voted_count']} eligible detectors agreeing "
                f"(weighted confidence {result_details['confidence']:.3f}). "
                "This is statistical evidence for human review, not a "
                "physical-failure diagnosis."
            )
    elif method != "threshold":
        raise ValueError(f"Unsupported equipment analysis method: {method}")

    if flatline_triggered:
        if limit_severity is None and not model_anomalous:
            anomaly_type = "flatline"
            severity = "medium"
            explanation = (
                "sensor reading is constant, possible stuck sensor. "
                "This is statistical evidence for human review, not a diagnosis."
            )
        else:
            explanation += (
                " The sensor reading is constant, possible stuck sensor."
            )

    is_anomalous = (
        limit_severity is not None or model_anomalous or flatline_triggered
    )
    base_alert_rules = []
    if limit_severity is not None:
        base_alert_rules.append("limit")
    if model_anomalous:
        base_alert_rules.append("model")
    if flatline_triggered:
        base_alert_rules.append("flatline")
    if model_anomalous and limit_severity is not None:
        explanation += (
            f" The configured {method.replace('_', ' ')} model also found "
            "statistical evidence of unusual behavior."
        )
    if baseline_note:
        config["analysis_note"] = baseline_note
    history_state = (
        "insufficient_history" if len(baseline) < history_required else "ready"
    )
    config["history_evaluation"] = {
        "state": history_state,
        "history_size": len(baseline),
        "history_required": history_required,
        "algorithm": method,
    }
    config["flatline_evaluation"] = flatline_result
    model_result.setdefault("history_state", history_state)
    model_result["flatline_evaluation"] = flatline_result

    feature_values = {
        "parameter": parameter.name,
        "value": value,
        "unit": measurement.unit,
        "measured_at": measurement.measured_at.isoformat(),
        "flatline_evaluation": flatline_result,
    }
    if method == "isolation_forest" and nasa_features is not None:
        feature_values.update(nasa_features)
    alert_state = evaluate_alert_state(
        db,
        measurement,
        history_size=len(baseline),
        history_required=history_required,
        is_anomalous=is_anomalous,
        alert_rules=base_alert_rules,
        severity=severity,
    )
    db.add(
        AnalysisResult(
            analysis_run_id=run.id,
            equipment_measurement_id=measurement.id,
            source_row_number=None,
            is_anomaly=is_anomalous,
            anomaly_score=score,
            history_state=history_state if method == "ensemble" else None,
            history_size=len(baseline) if method == "ensemble" else None,
            history_required=history_required if method == "ensemble" else None,
            result_details=model_result if method == "ensemble" else {},
            feature_values=feature_values,
        )
    )
    anomaly: Anomaly | None = None
    test_data = measurement.source_type == "simulated"
    if is_anomalous and anomaly_type is not None and severity is not None:
        detected_at = datetime.now(timezone.utc)
        if limit_severity is not None or anomaly_type == "flatline":
            computed_severity = severity
            severity_reason = (
                "Configured limit severity preserved."
                if limit_severity is not None
                else "Flatline severity preserved."
            )
        else:
            earlier_scores = [
                float(value)
                for value in db.scalars(
                    select(Anomaly.anomaly_score)
                    .join(EquipmentMeasurement, EquipmentMeasurement.id == Anomaly.equipment_measurement_id)
                    .where(
                        EquipmentMeasurement.parameter_id == parameter.id,
                        EquipmentMeasurement.measured_at < measurement.measured_at,
                        Anomaly.anomaly_score.is_not(None),
                    )
                    .order_by(EquipmentMeasurement.measured_at.desc())
                )
            ]
            computed_severity, severity_reason = percentile_severity(
                float(score), earlier_scores
            ) if score is not None else ("medium", "insufficient history for severity")
        anomaly = Anomaly(
            analysis_run_id=run.id,
            equipment_measurement_id=measurement.id,
            anomaly_type=anomaly_type,
            anomaly_score=score,
            severity=severity,
            computed_severity=computed_severity,
            severity_reason=severity_reason,
            status="new",
            observed_value=value,
            expected_range={
                **reference,
                "score_threshold": score_threshold,
                "flatline_evaluation": flatline_result,
            },
            feature_values=feature_values,
            description=explanation,
            alert_episode_id=alert_state.episode.id if alert_state.episode else None,
            alert_rules=alert_state.alert_rules,
            alert_priority=alert_state.priority,
            history_state=alert_state.history_state,
            history_size=alert_state.history_size,
            history_required=alert_state.history_required,
            detected_at=detected_at,
        )
        db.add(anomaly)
        db.flush()
        if alert_state.episode_created and alert_state.episode is not None:
            notification_prefix = "[SYNTHETIC TEST ONLY] " if test_data else ""
            notification_title = (
                f"{notification_prefix}Reading needs attention · "
                f"{alert_state.priority.title()}"
            )
            notification_message = (
                f"{notification_prefix}{device.name} · {parameter.name}: "
                f"{value:g} {measurement.unit}. {explanation} "
                f"Rules: {', '.join(alert_state.alert_rules)}. "
                "This is statistical evidence for review, not a diagnosis."
            )
            details = {
                "device": {
                    "id": device.id,
                    "name": device.name,
                    "code": device.device_code,
                    "source_type": measurement.source_type,
                },
                "detection": {
                    "detected_at": detected_at.isoformat(),
                    "measurement_time": measurement.measured_at.isoformat(),
                    "severity": severity,
                    "alert_priority": alert_state.priority,
                    "alert_rules": alert_state.alert_rules,
                    "flatline_evaluation": flatline_result,
                    "history_state": alert_state.history_state,
                    "history_size": alert_state.history_size,
                    "history_required": alert_state.history_required,
                },
                "measurement": {
                    parameter.name: value,
                    "value": value,
                },
                "measurement_unit": measurement.unit,
                "analysis": {
                    "run_id": run.id,
                    "algorithm": model_result.get("algorithm"),
                    "score": score,
                    "score_threshold": score_threshold,
                    "description": explanation,
                    "model_result": model_result,
                    "limit_result": {
                        "is_out_of_bounds": limit_severity is not None,
                        "severity": limit_severity,
                    },
                },
                "device_id": device.id,
                "parameter_id": parameter.id,
                "measurement_id": measurement.id,
                "analysis_run_id": run.id,
                "alert_episode_id": alert_state.episode.id,
                "alert_rules": alert_state.alert_rules,
                "flatline_evaluation": flatline_result,
                "alert_priority": alert_state.priority,
                "severity": severity,
                "is_test_data": test_data,
                "history_state": alert_state.history_state,
                "history_size": alert_state.history_size,
                "history_required": alert_state.history_required,
            }
            dedupe_key = f"alert-episode:{alert_state.episode.id}"
            db.add(
                AuditLog(
                    user_id=measurement.recorded_by,
                    action="alert",
                    entity_type="alert_episode",
                    entity_id=alert_state.episode.id,
                    details=details,
                )
            )
            notify_roles(
                db,
                {"administrator", "data_analyst", "maintenance_technician"},
                notification_title,
                notification_message,
                "anomaly_detected",
                "anomaly",
                anomaly.id,
                details={**details, "rate_limit_device_id": device.id},
                dedupe_key=dedupe_key,
            )
            notify_user(
                db,
                device.responsible_user_id,
                notification_title,
                notification_message,
                "anomaly_detected",
                "anomaly",
                anomaly.id,
                details={**details, "rate_limit_device_id": device.id},
                dedupe_key=dedupe_key,
            )

    run.total_records = 1
    run.anomalies_found = int(is_anomalous)
    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    run.metrics = {
        "configuration": config,
        "execution_seconds": max(0.0, time.perf_counter() - started_clock),
        "records_processed": 1,
        "anomalies_found": run.anomalies_found,
        "severity_distribution": {severity: 1} if is_anomalous and severity else {},
        "baseline_records": len(baseline),
        "measurement_timestamp": measurement.measured_at.isoformat(),
        "algorithm_score": score,
        "score_threshold": score_threshold,
        "model_result": model_result,
        "limit_result": {
            "is_out_of_bounds": limit_severity is not None,
            "severity": limit_severity,
        },
        "alert_evaluation": {
            "episode_id": alert_state.episode.id if alert_state.episode else None,
            "episode_status": alert_state.episode.status if alert_state.episode else None,
            "alert_rules": alert_state.alert_rules,
            "priority": alert_state.priority,
            "history_state": alert_state.history_state,
            "history_size": alert_state.history_size,
            "history_required": alert_state.history_required,
            "flatline_evaluation": flatline_result,
            "consecutive_anomalies": alert_state.consecutive_anomalies,
            "window_anomalies": alert_state.window_anomalies,
            "episode_created": alert_state.episode_created,
        },
    }
    return run
