from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.devices.application.measurement_severity import measurement_severity
from app.api.dependencies import get_current_user, get_db
from app.modules.devices.api.router import router as devices_router


def anomaly(detector, severity="medium", history_state="ready", alert_rules=None, anomaly_id=1, run_id=10):
    return SimpleNamespace(
        id=anomaly_id,
        analysis_run_id=run_id,
        anomaly_type=detector,
        severity=severity,
        history_state=history_state,
        alert_rules=alert_rules or [],
    )


def test_deduplicated_measurement_physical_limit_wins():
    result = measurement_severity([
        anomaly("threshold", "critical", anomaly_id=1),
        anomaly("isolation_forest", anomaly_id=2),
    ])
    assert result["severity"] == "critical"
    assert result["rule"]["fired"] == "physical_limit"


def test_statistical_result_is_never_critical():
    result = measurement_severity([
        anomaly("isolation_forest", anomaly_id=1),
        anomaly("z_score", anomaly_id=2),
        anomaly("ensemble", anomaly_id=3, alert_rules=["persistence"]),
    ])
    assert result["severity"] == "high"
    assert result["severity"] != "critical"


def test_two_and_one_detector_rules():
    two = measurement_severity([
        anomaly("isolation_forest", anomaly_id=1),
        anomaly("z_score", anomaly_id=2),
    ])
    one = measurement_severity([anomaly("isolation_forest", anomaly_id=1)])
    assert two["severity"] == "medium"
    assert two["agreeing_detectors"] == 2
    assert one["severity"] == "low"
    assert one["agreeing_detectors"] == 1


def test_three_detectors_without_persistence_are_not_high():
    result = measurement_severity([
        anomaly("isolation_forest", anomaly_id=1),
        anomaly("z_score", anomaly_id=2),
        anomaly("ensemble", anomaly_id=3),
    ])
    assert result["severity"] == "medium"


def test_insufficient_history_detectors_are_not_eligible():
    result = measurement_severity([
        anomaly("isolation_forest", history_state="insufficient_history", anomaly_id=1),
        anomaly("z_score", anomaly_id=2),
    ])
    assert result["detectors"] == ["z_score"]
    assert result["agreeing_detectors"] == 1
    assert result["severity"] == "low"


def test_flatline_severity_is_preserved():
    result = measurement_severity([anomaly("flatline", "high", anomaly_id=1)])
    assert result["severity"] == "high"
    assert result["rule"]["fired"] == "flatline"


def test_no_future_anomaly_is_used_by_measurement_aggregation():
    current = anomaly("isolation_forest", anomaly_id=1, run_id=10)
    future = SimpleNamespace(
        id=2,
        analysis_run_id=11,
        anomaly_type="z_score",
        severity="critical",
        history_state="ready",
        alert_rules=[],
        measured_at=21,
    )

    result = measurement_severity([current])
    assert future.measured_at > 20
    assert result["agreeing_detectors"] == 1
    assert result["severity"] == "low"


def test_measurement_severity_actions_reject_non_administrators():
    app = FastAPI()
    app.include_router(devices_router, prefix="/devices")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=8,
        role="data_analyst",
    )
    app.dependency_overrides[get_db] = Mock

    with TestClient(app) as client:
        dry_run = client.post("/devices/measurement-severity/dry-run")
        store = client.post("/devices/measurement-severity/store")

    assert dry_run.status_code == 403, dry_run.text
    assert store.status_code == 403, store.text
