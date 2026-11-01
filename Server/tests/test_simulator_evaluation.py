import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.modules.analyses.api.simulator_evaluations import router
from app.modules.analyses.application.simulator_evaluation import (
    GroundTruthValidationError,
    _fault_delays,
    _point_metrics,
    _rule_evidence,
    create_simulator_evaluation,
    evaluation_to_csv,
    import_simulator_ground_truth,
    load_development_ground_truth_runs,
)
from app.shared.database.base import Base
from app.shared.models import (
    AnalysisResult,
    AnalysisRun,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
)


class SimulatorEvaluationMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.rows = [
            SimpleNamespace(
                measurement_id=index + 1,
                device_id=1,
                parameter_id=1,
                device_code="SIM-TEST",
                parameter_name="Vibration RMS",
                simulated_time_seconds=float(index),
                received_at=self.origin + timedelta(seconds=index),
            )
            for index in range(6)
        ]
        self.intervals = [
            SimpleNamespace(
                fault_id="f1",
                device_id=1,
                parameter_id=1,
                device_code="SIM-TEST",
                fault_type="point_spike",
                start_seconds=2.0,
                end_seconds=2.0,
            ),
            SimpleNamespace(
                fault_id="f2",
                device_id=1,
                parameter_id=1,
                device_code="SIM-TEST",
                fault_type="slow_drift",
                start_seconds=4.0,
                end_seconds=4.0,
            ),
        ]

    @staticmethod
    def _result(anomaly: bool, score: float) -> SimpleNamespace:
        return SimpleNamespace(
            is_anomaly=anomaly,
            anomaly_score=score,
            result_details={"execution_mode": "walk_forward"},
        )

    def test_per_fault_metrics_exclude_other_fault_windows(self) -> None:
        results = {
            index + 1: self._result(index in (2, 4), 0.9 if index in (2, 4) else 0.1)
            for index in range(6)
        }

        overall, by_fault, scored = _point_metrics(
            self.rows, self.intervals, results, tolerance_readings=0
        )

        self.assertEqual(len(scored), 6)
        self.assertEqual(overall["sample_count"], 6)
        self.assertEqual(by_fault["point_spike"]["sample_count"], 5)
        self.assertEqual(by_fault["slow_drift"]["sample_count"], 5)
        self.assertEqual(by_fault["point_spike"]["confusion_matrix"]["true_positive"], 1)

    def test_warmup_exclusion_and_detection_delay_use_accepted_reading_positions(self) -> None:
        scored_by_id = {
            1: {"reading": self.rows[0], "result": self._result(False, 0.1)},
            3: {
                "reading": self.rows[2],
                "result": self._result(False, 0.2),
            },
            4: {
                "reading": self.rows[3],
                "result": self._result(True, 0.8),
            },
            5: {
                "reading": self.rows[4],
                "result": self._result(False, 0.1),
            },
            6: {
                "reading": self.rows[5],
                "result": self._result(False, 0.1),
            },
        }
        interval = [
            SimpleNamespace(
                fault_id="f",
                device_id=1,
                parameter_id=1,
                device_code="SIM-TEST",
                fault_type="point_spike",
                start_seconds=2.0,
                end_seconds=2.0,
            )
        ]

        delays = _fault_delays(
            list(scored_by_id.values()),
            self.rows,
            interval,
            tolerance_readings=1,
        )

        self.assertTrue(delays[0]["detected"])
        self.assertEqual(delays[0]["delay_readings"], 1)
        self.assertEqual(delays[0]["delay_seconds"], 1.0)

    def test_flatline_and_stopped_reporting_episode_evidence(self) -> None:
        intervals = [
            SimpleNamespace(
                fault_id="stuck",
                device_id=1,
                parameter_id=1,
                device_code="SIM-TEST",
                fault_type="stuck_sensor",
                start_seconds=1.0,
                end_seconds=3.0,
            ),
            SimpleNamespace(
                fault_id="dropout",
                device_id=1,
                parameter_id=1,
                device_code="SIM-TEST",
                fault_type="signal_dropout",
                start_seconds=4.0,
                end_seconds=4.5,
            ),
        ]
        episodes = [
            SimpleNamespace(
                id=3,
                device_id=1,
                parameter_id=1,
                alert_rules=["flatline"],
                status="open",
                first_detected_at=self.origin + timedelta(seconds=2),
                closed_at=None,
            ),
            SimpleNamespace(
                id=4,
                device_id=1,
                parameter_id=1,
                alert_rules=["stale_data"],
                status="closed",
                first_detected_at=self.origin + timedelta(seconds=4, milliseconds=500),
                closed_at=self.origin + timedelta(seconds=5),
            ),
        ]

        evidence = _rule_evidence(self.rows, intervals, episodes)

        self.assertTrue(evidence["flatline"][0]["detected"])
        self.assertEqual(evidence["flatline"][0]["readings_after_fault_start"], 1)
        self.assertTrue(evidence["stopped_reporting"][0]["detected"])
        self.assertEqual(evidence["stopped_reporting"][0]["status"], "closed")


class SimulatorGroundTruthPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.received_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.device = Device(
            name="Evaluation motor",
            device_code="SIM-EVALUATION-01",
            device_type="motor",
            source_type="simulated",
        )
        self.db.add(self.device)
        self.db.flush()
        self.parameter = EquipmentParameter(
            device_id=self.device.id,
            name="Vibration RMS",
            unit="mm/s",
        )
        self.db.add(self.parameter)
        self.db.flush()
        self.measurement = EquipmentMeasurement(
            parameter_id=self.parameter.id,
            value=10.0,
            unit="mm/s",
            measured_at=self.received_at,
            received_at=self.received_at,
            source="simulated",
            source_type="simulated",
            idempotency_key="sim-key-1",
        )
        self.db.add(self.measurement)
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _jsonl(self, *, fault_type: str = "point_spike") -> bytes:
        events = [
            {"event": "run_started", "run_id": "test-run-1", "seed": 55},
            {
                "event": "fault_scheduled",
                "run_id": "test-run-1",
                "seed": 55,
                "fault_id": "fault-1",
                "type": fault_type,
                "device_code": self.device.device_code,
                "parameter": self.parameter.name,
                "simulated_start_seconds": 0.0,
                "simulated_end_seconds": 0.0,
                "severity": "medium",
            },
            {
                "event": "reading_generated",
                "run_id": "test-run-1",
                "seed": 55,
                "idempotency_key": "sim-key-1",
                "device_code": self.device.device_code,
                "parameter": self.parameter.name,
                "simulated_time_seconds": 0.0,
                "active_fault_ids": ["fault-1"],
            },
            {
                "event": "reading_accepted",
                "run_id": "test-run-1",
                "seed": 55,
                "idempotency_key": "sim-key-1",
                "measurement_id": self.measurement.id,
                "device_code": self.device.device_code,
                "parameter": self.parameter.name,
                "received_at": self.received_at.isoformat(),
                "duplicate": False,
                "http_status": 200,
            },
            {"event": "run_finished", "run_id": "test-run-1", "seed": 55},
        ]
        return b"".join(
            json.dumps(event).encode("utf-8") + b"\n" for event in events
        )

    def test_import_persists_run_faults_and_linked_labels_then_rejects_duplicate(self) -> None:
        run = import_simulator_ground_truth(
            self.db,
            self._jsonl(),
            purpose="development",
            imported_by=1,
        )
        self.db.commit()

        self.assertEqual(run.run_id, "test-run-1")
        self.assertEqual(run.accepted_reading_count, 1)
        self.assertEqual(len(run.fault_intervals), 1)
        self.assertEqual(run.readings[0].measurement_id, self.measurement.id)
        with self.assertRaises(GroundTruthValidationError):
            import_simulator_ground_truth(
                self.db,
                self._jsonl(),
                purpose="development",
                imported_by=1,
            )

    def test_tuning_rejects_evaluation_runs_and_accepts_development_runs(self) -> None:
        evaluation_run = import_simulator_ground_truth(
            self.db,
            self._jsonl(),
            purpose="evaluation",
            imported_by=1,
        )
        with self.assertRaisesRegex(ValueError, "purpose='development'"):
            load_development_ground_truth_runs(self.db, [evaluation_run.id])

        development_jsonl = self._jsonl().replace(b"test-run-1", b"dev-run-1")
        development_run = import_simulator_ground_truth(
            self.db,
            development_jsonl,
            purpose="development",
            imported_by=1,
        )
        eligible_runs = load_development_ground_truth_runs(
            self.db, [development_run.id]
        )
        self.assertEqual([run.id for run in eligible_runs], [development_run.id])

    def test_import_rejects_non_simulated_measurements_and_unlabeled_runs(self) -> None:
        self.measurement.source_type = "sensor"
        self.db.flush()
        with self.assertRaises(GroundTruthValidationError):
            import_simulator_ground_truth(
                self.db,
                self._jsonl(),
                purpose="evaluation",
                imported_by=1,
            )

        events = [
            {"event": "run_started", "run_id": "no-labels", "seed": 3},
            {"event": "run_finished", "run_id": "no-labels", "seed": 3},
        ]
        with self.assertRaises(GroundTruthValidationError):
            import_simulator_ground_truth(
                self.db,
                b"\n".join(json.dumps(event).encode() for event in events),
                purpose="evaluation",
                imported_by=1,
            )

    def test_evaluation_persists_scores_configuration_limits_and_csv(self) -> None:
        ground_truth = import_simulator_ground_truth(
            self.db,
            self._jsonl(),
            purpose="evaluation",
            imported_by=1,
        )
        analysis = AnalysisRun(
            device_id=self.device.id,
            source_type="equipment",
            status="completed",
            metrics={
                "configuration": {
                    "algorithm": "z_score",
                    "data_source_type": "simulated",
                    "parameter_id": self.parameter.id,
                    "detector_parameters": {"threshold": 3},
                }
            },
        )
        self.db.add(analysis)
        self.db.flush()
        self.db.add(
            AnalysisResult(
                analysis_run_id=analysis.id,
                equipment_measurement_id=self.measurement.id,
                is_anomaly=True,
                anomaly_score=0.9,
                history_state="ready",
                result_details={"execution_mode": "walk_forward"},
            )
        )
        self.db.flush()

        evaluation = create_simulator_evaluation(
            self.db,
            runs=[
                {
                    "ground_truth_run_id": ground_truth.id,
                    "analysis_run_ids": [analysis.id],
                }
            ],
            tolerance_readings=2,
            created_by=1,
        )
        self.db.commit()

        self.assertEqual(evaluation.purpose, "evaluation")
        self.assertEqual(evaluation.result["source_type"], "simulated")
        self.assertEqual(evaluation.result["runs"][0]["seed"], 55)
        self.assertIn("must not be used to select", evaluation.limitations)
        self.assertIn("simulated", evaluation_to_csv(evaluation))


class SimulatorEvaluationRBACTests(unittest.TestCase):
    def test_ground_truth_import_requires_explicit_purpose(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/evaluations")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=5, role="administrator"
        )
        app.dependency_overrides[get_db] = lambda: Mock()

        with TestClient(app) as client:
            response = client.post(
                "/evaluations/ground-truth",
                files={"file": ("run.jsonl", b"", "application/jsonl")},
            )

        self.assertEqual(response.status_code, 422, response.text)

    def test_technician_cannot_import_or_create_evaluations(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/evaluations")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=5, role="maintenance_technician"
        )
        app.dependency_overrides[get_db] = lambda: None
        with TestClient(app) as client:
            import_response = client.post(
                "/evaluations/ground-truth?purpose=development",
                files={"file": ("run.jsonl", b"", "application/jsonl")},
            )
            create_response = client.post(
                "/evaluations",
                json={
                    "runs": [
                        {"ground_truth_run_id": 1, "analysis_run_ids": [1]}
                    ]
                },
            )

        self.assertEqual(import_response.status_code, 403)
        self.assertEqual(create_response.status_code, 403)

    def test_evaluation_pdf_export_is_audited(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/evaluations")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=5, role="administrator")
        db = Mock()
        db.get.return_value = SimpleNamespace(
            id=11,
            purpose="evaluation",
            configuration={"tolerance_readings": 5},
            limitations="Stored limitation",
        )
        app.dependency_overrides[get_db] = lambda: db

        with (
            patch(
                "app.modules.analyses.api.simulator_evaluations._evaluation_pdf",
                return_value=b"%PDF-1.4 test",
            ),
            TestClient(app) as client,
        ):
            response = client.get("/evaluations/11/pdf")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("application/pdf", response.headers["content-type"])
        self.assertEqual(db.add.call_args.args[0].action, "export")
        db.commit.assert_called_once()

    def test_technician_can_read_and_export_evaluations(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/evaluations")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=5, role="maintenance_technician"
        )
        evaluation = SimpleNamespace(
            id=11,
            created_by=1,
            purpose="development",
            configuration={"tolerance_readings": 5},
            ground_truth_run_ids=[2],
            analysis_run_ids=[3],
            limitations="Statistical evidence for human review.",
            result={"source_type": "simulated", "runs": [], "detectors": {}},
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        db = Mock()
        db.get.return_value = evaluation
        app.dependency_overrides[get_db] = lambda: db

        with TestClient(app) as client:
            read_response = client.get("/evaluations/11")
            export_response = client.get("/evaluations/11/csv")

        self.assertEqual(read_response.status_code, 200, read_response.text)
        self.assertEqual(read_response.json()["source_type"], "simulated")
        self.assertEqual(export_response.status_code, 200, export_response.text)
        self.assertIn("text/csv", export_response.headers["content-type"])
        db.add.assert_called_once()
        db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
