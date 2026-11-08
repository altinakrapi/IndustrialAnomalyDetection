from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user, get_db
from app.modules.analyses.api.router import router as analyses_router
from app.shared.database.base import Base
from app.shared.models import AnalysisRun, Anomaly, MonitoringSchedule
from app.modules.analyses.domain.run_kinds import classify_analysis_run


def _analysis(
    *,
    source_type: str,
    execution_type: str,
    total_records: int,
    metrics: dict | None = None,
    status: str = "completed",
    dataset_id: int | None = None,
    device_id: int | None = None,
) -> AnalysisRun:
    return AnalysisRun(
        dataset_id=dataset_id,
        device_id=device_id,
        source_type=source_type,
        execution_type=execution_type,
        total_records=total_records,
        anomalies_found=0,
        metrics=metrics or {},
        status=status,
        created_at=datetime.now(timezone.utc),
    )


def _client(role: str = "data_analyst") -> tuple[TestClient, Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    app = FastAPI()
    app.include_router(analyses_router, prefix="/analyses")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role=role, is_active=True
    )
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app), db


def test_run_kind_classification_covers_stored_run_shapes():
    cases = (
        (_analysis(source_type="equipment", execution_type="measurement", total_records=1), "live_reading"),
        (_analysis(source_type="equipment", execution_type="measurement", total_records=12), "on_demand_analysis"),
        (_analysis(source_type="equipment", execution_type="scheduled", total_records=12), "scheduled_cycle"),
        (_analysis(source_type="equipment", execution_type="simulated", total_records=12), "scheduled_cycle"),
        (_analysis(source_type="equipment", execution_type="history", total_records=12), "on_demand_analysis"),
        (_analysis(
            source_type="dataset",
            execution_type="manual",
            total_records=12,
            metrics={"configuration": {"nasa_ims_validation_id": 7}},
        ), "validation"),
    )
    for analysis, expected in cases:
        assert classify_analysis_run(analysis) == expected


def test_history_kind_filters_default_compatibility_and_pagination():
    client, db = _client()
    db.add_all(
        [
            _analysis(source_type="equipment", execution_type="measurement", total_records=1),
            _analysis(source_type="equipment", execution_type="measurement", total_records=1, status="failed"),
            AnalysisRun(
                source_type="equipment",
                execution_type="measurement",
                total_records=1,
                anomalies_found=0,
                metrics={},
                status="completed",
                created_at=datetime.now(timezone.utc) - timedelta(hours=26),
            ),
            _analysis(source_type="equipment", execution_type="manual", total_records=10, device_id=900),
            _analysis(
                source_type="dataset",
                execution_type="manual",
                total_records=20,
                metrics={"configuration": {"algorithm": "z_score"}},
                dataset_id=700,
            ),
        ]
    )
    db.commit()
    try:
        default = client.get("/analyses/history?limit=1&offset=0")
        assert default.status_code == 200
        assert default.json()["total"] == 5
        assert len(default.json()["items"]) == 1

        hidden = client.get("/analyses/history?exclude_live_readings=true")
        assert hidden.status_code == 200
        assert hidden.json()["total"] == 2
        assert all(item["run_kind"] != "live_reading" for item in hidden.json()["items"])

        live = client.get("/analyses/history?run_kind=live_reading")
        assert live.status_code == 200
        assert live.json()["total"] == 3
        assert all(item["run_kind"] == "live_reading" for item in live.json()["items"])

        equipment = client.get("/analyses/history?source_type=equipment")
        assert equipment.status_code == 200
        assert equipment.json()["total"] == 4
        assert client.get("/analyses/history?dataset_id=700").json()["total"] == 1
        assert client.get("/analyses/history?device_id=900").json()["total"] == 1
        assert client.get("/analyses/history?algorithm=z_score").json()["total"] == 1
        assert client.get("/analyses/history?status_filter=failed").json()["total"] == 1
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        assert client.get(f"/analyses/history?start_date={start}&end_date={end}").json()["total"] == 4

        page = client.get("/analyses/history?limit=1&offset=1")
        assert page.status_code == 200
        assert page.json()["total"] == 5
        assert len(page.json()["items"]) == 1
        assert client.get("/analyses/history?run_kind=unsupported").status_code == 422

        summary = client.get("/analyses/summary")
        assert summary.status_code == 200
        assert summary.json()["live_reading_24h"] == {"total": 2, "failed": 1}
    finally:
        db.close()
        client.close()


def test_summary_separates_dataset_and_equipment_and_deduplicates_measurements():
    client, db = _client()
    next_dataset_run = datetime.now(timezone.utc) + timedelta(hours=1)
    db.add(MonitoringSchedule(id=1, enabled=True, next_run_at=next_dataset_run))
    dataset_runs = [
        _analysis(source_type="dataset", execution_type="manual", total_records=20),
        _analysis(source_type="dataset", execution_type="manual", total_records=20),
    ]
    equipment_run = _analysis(source_type="equipment", execution_type="manual", total_records=8)
    db.add_all([*dataset_runs, equipment_run])
    db.flush()
    db.add_all(
        [
            Anomaly(
                analysis_run_id=dataset_runs[0].id,
                measurement_record_id=123,
                anomaly_type="detector_flag",
                severity="medium",
            ),
            Anomaly(
                analysis_run_id=dataset_runs[1].id,
                measurement_record_id=123,
                anomaly_type="detector_flag",
                severity="medium",
            ),
            Anomaly(
                analysis_run_id=equipment_run.id,
                equipment_measurement_id=456,
                anomaly_type="detector_flag",
                severity="medium",
            ),
        ]
    )
    db.commit()
    try:
        response = client.get("/analyses/summary")
        assert response.status_code == 200
        result = response.json()
        assert result["registered_equipment"]["analyses"] == 1
        assert result["registered_equipment"]["detections"] == 1
        assert result["registered_equipment"]["measurements_flagged_unique"] == 1
        assert result["datasets"]["analyses"] == 2
        assert result["datasets"]["detections"] == 2
        assert result["datasets"]["measurements_flagged_unique"] == 1
        assert result["registered_equipment"]["next_scheduled_execution"] is None
        assert result["datasets"]["next_scheduled_execution"] == (
            next_dataset_run.replace(tzinfo=None).isoformat()
        )
    finally:
        db.close()
        client.close()


def test_analysis_history_and_summary_remain_available_to_all_roles():
    for role in ("administrator", "data_analyst", "maintenance_technician"):
        client, db = _client(role)
        try:
            assert client.get("/analyses/history").status_code == 200
            assert client.get("/analyses/summary").status_code == 200
        finally:
            db.close()
            client.close()
