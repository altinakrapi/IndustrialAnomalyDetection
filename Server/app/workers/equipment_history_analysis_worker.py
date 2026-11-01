import logging
import time
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import insert, select

from app.modules.analyses.application.equipment_history import (
    score_equipment_history,
)
from app.modules.analyses.domain.detectors import get_detector
from app.shared.database.session import SessionLocal
from app.shared.models import (
    AnalysisResult,
    AnalysisRun,
    Anomaly,
    AuditLog,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
)

logger = logging.getLogger(__name__)


def run_equipment_history_analysis(analysis_id: int) -> None:
    db = SessionLocal()
    started = time.perf_counter()
    try:
        analysis = db.get(AnalysisRun, analysis_id)
        if analysis is None:
            logger.error("Equipment history analysis %s no longer exists", analysis_id)
            return
        configuration = analysis.metrics.get("configuration", {})
        analysis.status = "running"
        analysis.started_at = datetime.now(timezone.utc)
        db.commit()

        device = db.get(Device, analysis.device_id)
        parameter = db.get(EquipmentParameter, configuration["parameter_id"])
        if (
            device is None
            or parameter is None
            or parameter.device_id != device.id
        ):
            raise ValueError("The registered device or parameter no longer exists")
        measurement_ids = configuration["measurement_ids"]
        measurements_by_id = {
            item.id: item
            for item in db.scalars(
                select(EquipmentMeasurement).where(
                    EquipmentMeasurement.id.in_(measurement_ids),
                    EquipmentMeasurement.parameter_id == parameter.id,
                    EquipmentMeasurement.source_type
                    == configuration["data_source_type"],
                )
            )
        }
        measurements = [
            measurements_by_id[measurement_id]
            for measurement_id in measurement_ids
            if measurement_id in measurements_by_id
        ]
        if len(measurements) != len(measurement_ids):
            raise ValueError("The selected equipment history changed before analysis")

        algorithm = configuration["algorithm"]
        detector = get_detector(algorithm)
        values = np.asarray(
            [measurement.value for measurement in measurements],
            dtype=np.float64,
        )
        scored = score_equipment_history(
            detector,
            values,
            parameters=configuration["detector_parameters"],
            feature_name=parameter.name,
            training_window=configuration["training_window"],
            refit_every=configuration["refit_every"],
        )
        result_rows = []
        anomaly_rows = []
        flagged_count = 0
        insufficient_count = 0
        for measurement, item in zip(measurements, scored, strict=True):
            is_anomaly = bool(item["is_anomaly"])
            flagged_count += int(is_anomaly)
            insufficient_count += int(
                item["history_state"] == "insufficient_history"
            )
            details = {
                **item["details"],
                "algorithm": algorithm,
                "execution_mode": item["execution_mode"],
                "score_threshold": item["score_threshold"],
                "severity_thresholds": item["severity_thresholds"],
                "source_type": measurement.source_type,
                "measurement_timestamp": measurement.measured_at.isoformat(),
                "fired_rule": "model" if is_anomaly else None,
            }
            if is_anomaly:
                thresholds = item["severity_thresholds"] or []
                if algorithm == "ensemble":
                    severity = (
                        "high"
                        if details.get("high_confidence", False)
                        else "medium"
                    )
                    history_size = int(item["history_size"])
                    if history_size >= 20 and len(thresholds) >= 2:
                        computed_severity = (
                            "critical" if float(item["score"]) >= float(thresholds[1])
                            else "high" if float(item["score"]) >= float(thresholds[0])
                            else "medium"
                        )
                        severity_reason = (
                            "Severity from earlier ensemble scores "
                            f"({history_size} earlier scores)."
                        )
                    else:
                        computed_severity = "medium"
                        severity_reason = "insufficient history for severity"
                else:
                    severity = (
                        "critical"
                        if len(thresholds) > 1 and item["score"] >= thresholds[1]
                        else "high"
                        if thresholds and item["score"] >= thresholds[0]
                        else "medium"
                    )
                    history_size = int(item["history_size"])
                    if history_size >= 20 and len(thresholds) >= 2:
                        computed_severity = (
                            "critical" if float(item["score"]) >= float(thresholds[1])
                            else "high" if float(item["score"]) >= float(thresholds[0])
                            else "medium"
                        )
                        severity_reason = (
                            "Severity from earlier detector score percentiles "
                            f"({history_size} earlier scores)."
                        )
                    else:
                        computed_severity = "medium"
                        severity_reason = "insufficient history for severity"
                details["severity"] = severity
                anomaly_rows.append(
                    {
                        "analysis_run_id": analysis.id,
                        "measurement_id": None,
                        "measurement_record_id": None,
                        "equipment_measurement_id": measurement.id,
                        "anomaly_type": algorithm,
                        "anomaly_score": item["score"],
                        "severity": severity,
                        "computed_severity": computed_severity,
                        "severity_reason": severity_reason,
                        "status": "new",
                        "observed_value": float(measurement.value),
                        "expected_range": {
                            "score_threshold": item["score_threshold"],
                            "severity_thresholds": thresholds,
                            "evidence_type": "statistical",
                        },
                        "feature_values": {
                            parameter.name: float(measurement.value),
                        },
                        "description": (
                            f"{algorithm} flagged this reading as unusual "
                            f"(score {item['score']:.4f}). This is statistical "
                            "evidence for human review, not a physical-failure diagnosis."
                        ),
                        "alert_rules": ["model"],
                        "alert_priority": None,
                        "history_state": item["history_state"],
                        "history_size": item["history_size"],
                        "history_required": item["history_required"],
                    }
                )
            result_rows.append(
                {
                    "analysis_run_id": analysis.id,
                    "measurement_record_id": None,
                    "source_row_number": None,
                    "equipment_measurement_id": measurement.id,
                    "is_anomaly": is_anomaly,
                    "anomaly_score": item["score"],
                    "history_state": item["history_state"],
                    "history_size": item["history_size"],
                    "history_required": item["history_required"],
                    "result_details": details,
                    "feature_values": {
                        parameter.name: float(measurement.value),
                    },
                }
            )
        if result_rows:
            db.execute(insert(AnalysisResult), result_rows)
        if anomaly_rows:
            db.execute(insert(Anomaly), anomaly_rows)
        analysis.total_records = len(measurements)
        analysis.anomalies_found = flagged_count
        analysis.metrics = {
            **analysis.metrics,
            "execution_seconds": round(time.perf_counter() - started, 4),
            "records_processed": len(measurements),
            "anomalies_found": flagged_count,
            "history_evaluation": {
                "state": "sufficient" if insufficient_count < len(measurements) else "insufficient_history",
                "available": len(measurements),
                "required": detector.minimum_history(
                    configuration["detector_parameters"]
                ),
                "insufficient_history_rows": insufficient_count,
            },
            "scoring": {
                "fit_scope": "earlier readings only; chronological walk-forward batches",
                "training_window": configuration["training_window"],
                "refit_every": configuration["refit_every"],
                "wording": "statistical evidence for human review",
            },
        }
        analysis.status = "completed"
        analysis.completed_at = datetime.now(timezone.utc)
        db.add(
            AuditLog(
                user_id=analysis.started_by,
                action="complete",
                entity_type="analysis",
                entity_id=analysis.id,
                details={
                    "source_type": configuration["data_source_type"],
                    "algorithm": algorithm,
                    "records_processed": len(measurements),
                    "anomalies_found": flagged_count,
                },
            )
        )
        db.commit()
    except Exception as error:
        db.rollback()
        analysis = db.get(AnalysisRun, analysis_id)
        if analysis is not None:
            analysis.status = "failed"
            analysis.error_message = str(error)[:2000]
            analysis.completed_at = datetime.now(timezone.utc)
            db.add(
                AuditLog(
                    user_id=analysis.started_by,
                    action="fail",
                    entity_type="analysis",
                    entity_id=analysis.id,
                    details={"error": str(error)[:1000]},
                )
            )
            db.commit()
        logger.exception("Equipment history analysis %s failed", analysis_id)
    finally:
        db.close()
