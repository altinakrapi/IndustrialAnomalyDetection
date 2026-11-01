from collections.abc import Iterable
from typing import Any


SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
CONTROL_CHART_LAG_READINGS = 5


def measurement_severity(anomalies: Iterable[Any]) -> dict:
    rows = list(anomalies)
    if not rows:
        raise ValueError("At least one anomaly is required")

    threshold_rows = [row for row in rows if row.anomaly_type == "threshold"]
    if threshold_rows:
        severity = max(
            (str(row.severity).lower() for row in threshold_rows),
            key=lambda value: SEVERITY_RANK.get(value, -1),
        )
        return {
            "severity": severity,
            "rule": {
                "fired": "physical_limit",
                "description": "Physical limit takes precedence over statistical evidence.",
            },
            "detectors": sorted({str(row.anomaly_type) for row in threshold_rows}),
            "agreeing_detectors": len(threshold_rows),
            "analysis_run_ids": sorted({row.analysis_run_id for row in rows}),
            "anomaly_ids": sorted({row.id for row in rows}),
        }

    flatline_rows = [row for row in rows if row.anomaly_type == "flatline"]
    if flatline_rows:
        severity = str(max(flatline_rows, key=lambda row: SEVERITY_RANK.get(str(row.severity).lower(), -1)).severity).lower()
        return {
            "severity": severity,
            "rule": {
                "fired": "flatline",
                "description": "Flatline keeps its existing severity.",
            },
            "detectors": sorted({str(row.anomaly_type) for row in flatline_rows}),
            "agreeing_detectors": len(flatline_rows),
            "analysis_run_ids": sorted({row.analysis_run_id for row in rows}),
            "anomaly_ids": sorted({row.id for row in rows}),
        }

    eligible = [
        row for row in rows
        if row.anomaly_type not in {"threshold", "flatline"}
        and getattr(row, "history_state", None) != "insufficient_history"
    ]
    detectors = sorted({str(row.anomaly_type) for row in eligible})
    agreeing = len(detectors)
    persistence_fired = any(
        "persistence" in (getattr(row, "alert_rules", None) or [])
        for row in eligible
    )

    if agreeing >= 3 and persistence_fired:
        severity = "high"
        rule = "at least 3 eligible detectors agreed with persistence"
    elif agreeing >= 3:
        severity = "medium"
        rule = "3 or more eligible detectors agreed without persistence"
    elif agreeing == 2:
        severity = "medium"
        rule = "exactly 2 eligible detectors agreed"
    elif agreeing == 1:
        severity = "low"
        rule = "1 eligible detector agreed"
    else:
        severity = "low"
        rule = "no eligible statistical detectors"

    return {
        "severity": severity,
        "rule": {
            "fired": "statistical",
            "description": rule,
        },
        "detectors": detectors,
        "agreeing_detectors": agreeing,
        "analysis_run_ids": sorted({row.analysis_run_id for row in rows}),
        "anomaly_ids": sorted({row.id for row in rows}),
    }
