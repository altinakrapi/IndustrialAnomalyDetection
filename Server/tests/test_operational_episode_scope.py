from datetime import datetime, timedelta, timezone
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_db
from app.modules.anomaly_detection.api.router import (
    _anomaly_query,
    router as anomaly_router,
)
from app.modules.anomaly_detection.application.device_anomaly_service import (
    grouped_device_summary,
)
from app.modules.anomaly_detection.application.episode_backfill_preview import (
    dry_run_live_episode_link_candidates,
)
from app.modules.dashboard.api.router import dashboard
from app.shared.database.base import Base
from app.shared.models import (
    AlertEpisode,
    AnalysisComparison,
    AnalysisRun,
    Anomaly,
    Dataset,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    MeasurementRecord,
    NasaIMSValidation,
    SimulatorEvaluation,
    SimulatorTunedConfig,
)


class OperationalEpisodeScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = SimpleNamespace(id=1, role="data_analyst")
        self.detected_at = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)

        device = Device(
            name="Operational pump",
            device_code="OPERATIONAL-PUMP",
            device_type="pump",
            source_type="simulated",
            is_registered=True,
        )
        archived_device = Device(
            name="Archived pump",
            device_code="ARCHIVED-PUMP",
            device_type="pump",
            source_type="manual",
            is_registered=True,
            status="archived",
        )
        self.db.add_all([device, archived_device])
        self.db.flush()
        parameter = EquipmentParameter(
            device_id=device.id,
            name="Vibration RMS",
            unit="mm/s",
            enabled=False,
        )
        archived_parameter = EquipmentParameter(
            device_id=archived_device.id,
            name="Vibration RMS",
            unit="mm/s",
            enabled=False,
        )
        self.db.add_all([parameter, archived_parameter])
        self.db.flush()

        measurement = self._measurement(parameter, self.detected_at)
        archived_measurement = self._measurement(
            archived_parameter, self.detected_at
        )
        self.db.add_all([measurement, archived_measurement])
        self.db.flush()

        live_run = self._run(device.id, measurement.id, "measurement")
        self._anomaly(live_run, measurement.id, self.detected_at)
        archived_run = self._run(
            archived_device.id, archived_measurement.id, "measurement"
        )
        self._anomaly(archived_run, archived_measurement.id, self.detected_at)

        excluded_runs = []
        for kind, purpose in (
            ("manual", "evaluation"),
            ("history", "tuning"),
            ("manual", "comparison"),
            ("history", "validation"),
        ):
            run = self._run(device.id, measurement.id, kind)
            self._anomaly(run, measurement.id, self.detected_at)
            excluded_runs.append((run, purpose))
        purpose_live_runs = {}
        for purpose in ("evaluation", "tuning", "comparison", "validation"):
            run = self._run(device.id, measurement.id, "measurement")
            self._anomaly(run, measurement.id, self.detected_at)
            purpose_live_runs[purpose] = run

        self.db.add(
            AlertEpisode(
                device_id=device.id,
                parameter_id=parameter.id,
                status="open",
                priority="watch",
                severity="medium",
                alert_rules=["model"],
                measurement_ids=[measurement.id],
                history_size=1,
                history_required=1,
                consecutive_anomalies=1,
                window_anomalies=1,
                first_detected_at=self.detected_at - timedelta(minutes=1),
                last_detected_at=self.detected_at + timedelta(minutes=1),
            )
        )

        dataset = Dataset(
            name="NASA IMS test",
            file_path="nasa-ims.zip",
            file_format="nasa_ims",
        )
        self.db.add(dataset)
        self.db.flush()
        record = MeasurementRecord(
            dataset_id=dataset.id,
            source_row_number=1,
            values={"bearing_1_rms": 5.0},
        )
        self.db.add(record)
        self.db.flush()
        dataset_run = AnalysisRun(
            dataset_id=dataset.id,
            source_type="dataset",
            execution_type="manual",
            status="completed",
            metrics={},
        )
        self.db.add(dataset_run)
        self.db.flush()
        self.db.add(
            Anomaly(
                analysis_run_id=dataset_run.id,
                measurement_record_id=record.id,
                anomaly_type="isolation_forest",
                severity="high",
                status="new",
                feature_values={},
                expected_range={},
                detected_at=self.detected_at,
            )
        )
        self.db.flush()

        evaluation_run, tuning_run, comparison_run, validation_run = [
            item[0] for item in excluded_runs
        ]
        self.db.add_all(
            [
                SimulatorEvaluation(
                    purpose="evaluation",
                    configuration={},
                    ground_truth_run_ids=[],
                    analysis_run_ids=[
                        evaluation_run.id,
                        purpose_live_runs["evaluation"].id,
                    ],
                    limitations="test",
                    result={},
                ),
                SimulatorTunedConfig(
                    name="scope-test-config",
                    algorithm="z_score",
                    parameters={},
                    objective="minimize_false_alarms",
                    false_alarm_cap=0.1,
                    development_run_ids=[
                        tuning_run.id,
                        purpose_live_runs["tuning"].id,
                    ],
                    development_seeds=[],
                    limitations="test",
                ),
                AnalysisComparison(
                    analysis_ids=[
                        comparison_run.id,
                        purpose_live_runs["comparison"].id,
                    ],
                    snapshot={},
                ),
                NasaIMSValidation(
                    dataset_id=dataset.id,
                    status="queued",
                    configuration={},
                    analysis_run_ids=[
                        validation_run.id,
                        purpose_live_runs["validation"].id,
                    ],
                    result={},
                    limitations="test",
                ),
            ]
        )
        self.db.commit()
        self.device = device

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _measurement(self, parameter, measured_at):
        return EquipmentMeasurement(
            parameter_id=parameter.id,
            value=2.4,
            unit="mm/s",
            measured_at=measured_at,
            source="simulated",
            source_type="simulated",
        )

    def _run(self, device_id, measurement_id, execution_type):
        run = AnalysisRun(
            device_id=device_id,
            equipment_measurement_id=measurement_id,
            source_type="equipment",
            execution_type=execution_type,
            status="completed",
            metrics={},
        )
        self.db.add(run)
        self.db.flush()
        return run

    def _anomaly(self, run, measurement_id, detected_at):
        self.db.add(
            Anomaly(
                analysis_run_id=run.id,
                equipment_measurement_id=measurement_id,
                anomaly_type="z_score",
                severity="medium",
                status="new",
                feature_values={"Vibration RMS": 2.4},
                expected_range={},
                detected_at=detected_at,
            )
        )
        self.db.flush()

    def test_grouped_flat_and_dashboard_share_operational_scope(self) -> None:
        later_device = Device(
            name="Later pump",
            device_code="LATER-PUMP",
            device_type="pump",
            source_type="manual",
            is_registered=True,
        )
        self.db.add(later_device)
        self.db.flush()
        later_parameter = EquipmentParameter(
            device_id=later_device.id,
            name="Temperature",
            unit="C",
            enabled=False,
        )
        self.db.add(later_parameter)
        self.db.flush()
        later_measurement = self._measurement(
            later_parameter, self.detected_at + timedelta(days=1)
        )
        self.db.add(later_measurement)
        self.db.flush()
        later_run = self._run(
            later_device.id, later_measurement.id, "measurement"
        )
        self._anomaly(
            later_run,
            later_measurement.id,
            self.detected_at + timedelta(days=1),
        )
        self.db.commit()

        filters = {
            "db": self.db,
            "source_type": "equipment",
            "include_test_data": True,
            "include_dataset": False,
            "measurement_source_type": None,
            "status_filter": None,
            "device_id": None,
            "dataset_id": None,
            "analysis_id": None,
            "severity": None,
            "algorithm": None,
            "parameter": None,
            "search": None,
            "start_date": None,
            "end_date": None,
        }
        grouped = grouped_device_summary(
            db=self.db,
            anomaly_query=_anomaly_query,
            user=self.user,
            filters=filters,
        )
        app = FastAPI()
        app.include_router(anomaly_router, prefix="/anomalies")
        app.dependency_overrides[get_current_user] = lambda: self.user

        def db_override():
            yield self.db

        app.dependency_overrides[get_db] = db_override
        with TestClient(app) as client:
            grouped_response = client.get(
                "/anomalies/devices/grouped",
                params={"include_test_data": "true"},
            )
            flat_response = client.get(
                "/anomalies/page",
                params={
                    "include_test_data": "true",
                    "limit": 50,
                    "offset": 0,
                },
            )
        self.assertEqual(grouped_response.status_code, 200)
        self.assertEqual(flat_response.status_code, 200)
        grouped_route = grouped_response.json()
        flat = flat_response.json()
        dashboard_data = dashboard(user=self.user, db=self.db)

        self.assertEqual(grouped["total"], 2)
        self.assertEqual(grouped["measurement_total"], 2)
        self.assertEqual(sum(row["anomaly_count"] for row in grouped["devices"]), 2)
        self.assertEqual(grouped_route["total"], grouped["total"])
        self.assertEqual(grouped_route["measurement_total"], grouped["measurement_total"])
        self.assertEqual(flat["anomaly_row_total"], grouped_route["total"])
        self.assertEqual(flat["total"], grouped_route["measurement_total"])
        self.assertEqual(grouped_route["devices"][0]["device_name"], "Later pump")
        self.assertEqual(dashboard_data["metrics"]["equipment_anomalies"], 2)
        self.assertEqual(
            sum(row["count"] for row in dashboard_data["anomalies_by_device"]),
            grouped["total"],
        )
        self.assertEqual(dashboard_data["metrics"]["unreviewed_equipment_anomalies"], 2)

    def test_dry_run_only_considers_live_runs_and_never_writes_links(self) -> None:
        result = dry_run_live_episode_link_candidates(self.db)
        self.assertEqual(
            result,
            {
                "dry_run_only": True,
                "eligible_live_rows": 1,
                "one_episode_match": 1,
                "ambiguous_episode_matches": 0,
                "no_episode_match": 0,
                "writes_performed": 0,
            },
        )
        live_anomaly = self.db.scalar(
            select(Anomaly).join(AnalysisRun).where(
                AnalysisRun.execution_type == "measurement",
                AnalysisRun.device_id == self.device.id,
            )
        )
        self.assertIsNone(live_anomaly.alert_episode_id)


if __name__ == "__main__":
    unittest.main()
