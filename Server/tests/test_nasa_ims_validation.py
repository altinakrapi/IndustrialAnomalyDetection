import unittest
import csv
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.modules.datasets.api.nasa_ims_validation import router
from app.modules.datasets.application.nasa_ims_validation import (
    LIMITATIONS,
    NasaIMSValidationInProgress,
    analysis_features_for_detector,
    create_nasa_ims_validation,
    features_for_bearing,
    summarize_bearing_series,
    nasa_ims_test_set_for_dataset_name,
    nasa_ims_validation_to_csv,
)
from app.shared.database.base import Base
from app.shared.database.migrations.versions.b1c2d3e4f5a6_add_nasa_ims_validation import (
    _fact_rows,
)
from app.shared.models import Dataset, NasaIMSBearingFact, NasaIMSValidation
from app.workers.analysis_worker import analysis_notifications_enabled


class NasaIMSValidationMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 2, 1, tzinfo=timezone.utc)
        self.records = [
            (position + 1, self.start + timedelta(seconds=position))
            for position in range(10)
        ]

    def test_sustained_alert_and_lead_time_use_consecutive_flagged_windows(self) -> None:
        flags = {1: False, 2: True, 3: True, 4: True, 5: False, 6: True}
        summary = summarize_bearing_series(
            self.records,
            flags,
            healthy_period_fraction=0.2,
            sustained_alert_windows=3,
            time_bin_count=5,
        )

        self.assertTrue(summary["sustained_alert"]["detected"])
        self.assertEqual(summary["sustained_alert"]["first_detected_source_row"], 4)
        self.assertEqual(summary["sustained_alert"]["first_detected_at"], self.records[3][1].isoformat())
        self.assertEqual(summary["lead_time_to_test_end_seconds"], 6)

    def test_single_noisy_snapshot_does_not_trigger_snapshot_alert(self) -> None:
        records = [
            (position + 1, self.start + timedelta(seconds=position), f"snapshot-{snapshot}")
            for snapshot, window_count in enumerate([1, 2, 3, 4])
            for position in range(window_count)
        ]
        records = [
            (position + 1, timestamp, snapshot)
            for position, (timestamp, snapshot) in enumerate(
                (
                    (self.start + timedelta(seconds=index * 10), f"snapshot-{index}")
                    for index in range(4)
                )
            )
            for _ in range(1)
        ]
        flags = {1: True, 2: False, 3: False, 4: False}

        summary = summarize_bearing_series(
            records,
            flags,
            healthy_period_fraction=0.2,
            sustained_alert_windows=3,
            snapshot_flag_fraction=0.2,
            sustained_alert_snapshots=3,
            time_bin_count=5,
            snapshot_burn_in_snapshots=0,
        )

        self.assertFalse(summary["snapshot_aggregation"]["sustained_alert"]["detected"])

    def test_three_consecutive_degraded_snapshots_trigger_snapshot_alert(self) -> None:
        records = [
            (index + 1, self.start + timedelta(seconds=index), f"snapshot-{index // 10}")
            for index in range(30)
        ]
        flags = {
            position: position <= 30 and position // 10 in {0, 1, 2}
            for position in range(1, 31)
        }

        summary = summarize_bearing_series(
            records,
            flags,
            healthy_period_fraction=0.2,
            sustained_alert_windows=3,
            snapshot_flag_fraction=0.2,
            sustained_alert_snapshots=3,
            time_bin_count=5,
            snapshot_burn_in_snapshots=0,
        )

        self.assertTrue(summary["snapshot_aggregation"]["sustained_alert"]["detected"])

    def test_flagged_burst_inside_snapshot_burn_in_does_not_trigger_alert(self) -> None:
        records = [
            (index + 1, self.start + timedelta(seconds=index), f"snapshot-{index}")
            for index in range(6)
        ]
        flags = {1: True, 2: True, 3: True, 4: False, 5: False, 6: False}

        summary = summarize_bearing_series(
            records,
            flags,
            healthy_period_fraction=0.2,
            sustained_alert_windows=3,
            snapshot_flag_fraction=1.0,
            sustained_alert_snapshots=3,
            snapshot_burn_in_snapshots=3,
            time_bin_count=5,
        )

        self.assertFalse(summary["snapshot_aggregation"]["sustained_alert"]["detected"])

    def test_shared_multi_feature_csv_marks_bearings_not_attributable(self) -> None:
        metrics = {
            "overall_anomaly_rate": 0.0614,
            "healthy_period": {
                "fraction_of_test": 0.2,
                "false_alarm_rate": 0.0,
                "flagged_windows": 0,
                "scored_windows": 10,
            },
            "sustained_alert": {
                "detected": True,
                "required_consecutive_windows": 5,
                "first_detected_at": None,
            },
            "lead_time_to_test_end_seconds": None,
            "snapshot_aggregation": {
                "flagged_share_threshold": 0.2,
                "required_consecutive_snapshots": 3,
                "sustained_alert": {"detected": True, "first_detected_at": None},
                "lead_time_to_test_end_seconds": 3600,
                "healthy_period_false_alarm_proxy": {"false_alarm_rate": 0.0},
            },
            "snapshot_burn_in": {
                "configured_snapshots": 63,
            },
            "scored_windows": 10,
            "flagged_windows": 1,
            "time_bins": [],
        }
        validation = SimpleNamespace(
            result={
                "test_set": "2nd_test",
                "verified_bearing_facts": [
                    {
                        "bearing_number": bearing,
                        "is_failed": bearing == 1,
                        "failure_type": "outer_race" if bearing == 1 else None,
                    }
                    for bearing in range(1, 5)
                ],
                "detectors": {
                    "isolation_forest_multi_feature": {
                        "execution_mode": "batch",
                        "bearings": {"1": metrics},
                    }
                },
            },
            limitations="Test limitations",
        )

        rows = list(csv.DictReader(nasa_ims_validation_to_csv(validation).splitlines()))
        self.assertEqual(
            rows[0]["bearing_attribution"],
            "not_attributable_per_bearing",
        )

    def test_unscored_windows_break_a_sustained_alert_streak(self) -> None:
        flags = {1: True, 3: True, 4: True, 5: True}
        summary = summarize_bearing_series(
            self.records,
            flags,
            healthy_period_fraction=0.2,
            sustained_alert_windows=3,
            time_bin_count=5,
        )

        self.assertTrue(summary["sustained_alert"]["detected"])
        self.assertEqual(summary["sustained_alert"]["first_detected_source_row"], 5)
        self.assertEqual(summary["unscored_windows"], 6)

    def test_healthy_period_false_alarm_rate_uses_only_scored_early_windows(self) -> None:
        flags = {1: False, 2: True, 3: True, 4: False, 5: True}
        summary = summarize_bearing_series(
            self.records,
            flags,
            healthy_period_fraction=0.2,
            sustained_alert_windows=5,
            time_bin_count=5,
        )

        self.assertEqual(summary["healthy_period"]["scored_windows"], 2)
        self.assertEqual(summary["healthy_period"]["flagged_windows"], 1)
        self.assertEqual(summary["healthy_period"]["false_alarm_rate"], 0.5)

    def test_time_binning_counts_flagged_and_scored_windows(self) -> None:
        flags = {position: position in {1, 2, 9, 10} for position in range(1, 11)}
        summary = summarize_bearing_series(
            self.records,
            flags,
            healthy_period_fraction=0.2,
            sustained_alert_windows=5,
            time_bin_count=5,
        )

        self.assertEqual([item["scored_windows"] for item in summary["time_bins"]], [2, 2, 2, 2, 2])
        self.assertEqual([item["flagged_windows"] for item in summary["time_bins"]], [2, 0, 0, 0, 2])
        self.assertEqual([item["anomaly_rate"] for item in summary["time_bins"]], [1.0, 0.0, 0.0, 0.0, 1.0])

    def test_bearing_mapping_matches_set_specific_channel_layout(self) -> None:
        self.assertEqual(nasa_ims_test_set_for_dataset_name("NASA IMS Bearings - 1st_test"), "1st_test")
        self.assertEqual(nasa_ims_test_set_for_dataset_name("NASA IMS Bearings - 2nd_test"), "2nd_test")
        self.assertEqual(features_for_bearing("1st_test", [5, 6])[0], "sensor_5_rms")
        self.assertEqual(len(features_for_bearing("1st_test", [5, 6])), 18)
        self.assertEqual(features_for_bearing("2nd_test", [1])[0], "bearing_1_rms")
        self.assertEqual(len(features_for_bearing("2nd_test", [1])), 9)

    def test_control_charts_use_one_rms_feature_per_bearing(self) -> None:
        bearing_features = features_for_bearing("1st_test", [5, 6])

        self.assertEqual(
            analysis_features_for_detector("ewma", bearing_features),
            ["sensor_5_rms"],
        )
        self.assertEqual(
            analysis_features_for_detector("cusum", bearing_features),
            ["sensor_5_rms"],
        )
        self.assertEqual(
            analysis_features_for_detector("isolation_forest", bearing_features),
            bearing_features,
        )
        with self.assertRaisesRegex(ValueError, "requires an RMS feature"):
            analysis_features_for_detector("ewma", ["bearing_1_std"])


    def test_migration_seeds_verified_channel_and_failure_facts(self) -> None:
        rows = _fact_rows()
        by_set = {}
        for row in rows:
            by_set.setdefault(row["test_set"], []).append(row)

        self.assertEqual({key: len(value) for key, value in by_set.items()}, {
            "1st_test": 4,
            "2nd_test": 4,
            "3rd_test": 4,
        })
        self.assertEqual(
            by_set["1st_test"][2]["channel_numbers"],
            [5, 6],
        )
        self.assertEqual(
            [(row["bearing_number"], row["failure_type"]) for row in by_set["2nd_test"] if row["is_failed"]],
            [(1, "outer_race")],
        )
        self.assertEqual(
            (by_set["2nd_test"][0]["healthy_bearing_count"], by_set["2nd_test"][0]["failed_bearing_count"]),
            (3, 1),
        )
        self.assertTrue(all(row["source_document"].endswith(".pdf") for row in rows))

    def test_analysis_notification_suppression_is_opt_in(self) -> None:
        self.assertTrue(analysis_notifications_enabled({}))
        self.assertFalse(analysis_notifications_enabled({"suppress_notifications": True}))


class NasaIMSValidationPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.dataset = Dataset(
            name="NASA IMS Bearings - 2nd_test",
            file_path="unused.zip",
            file_format="nasa_ims",
            row_count=100,
            column_metadata=[
                {"name": feature, "type": "numeric"}
                for bearing in range(1, 5)
                for feature in features_for_bearing("2nd_test", [bearing])
            ],
            status="uploaded",
        )
        self.db.add(self.dataset)
        self.db.flush()
        for row in _fact_rows():
            if row["test_set"] == "2nd_test":
                self.db.add(NasaIMSBearingFact(**row))
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_create_persists_all_detector_bearing_runs_and_configuration(self) -> None:
        validation = create_nasa_ims_validation(
            self.db,
            dataset_id=self.dataset.id,
            healthy_period_fraction=0.2,
            sustained_alert_windows=5,
            time_bin_count=100,
            created_by=1,
        )

        self.assertEqual(validation.status, "queued")
        self.assertEqual(len(validation.analysis_run_ids), 36)
        self.assertEqual(validation.configuration["healthy_period_fraction"], 0.2)
        self.assertEqual(validation.configuration["sustained_alert_windows"], 5)
        self.assertIn(
            "assumes the configured segment is healthy",
            validation.limitations,
        )
        self.assertEqual(
            validation.configuration["analysis_features"]["ewma"]["1"],
            ["bearing_1_rms"],
        )
        self.assertEqual(
            validation.configuration["analysis_features"]["cusum"]["4"],
            ["bearing_4_rms"],
        )
        self.assertEqual(set(validation.configuration["detector_settings"]), {
            "isolation_forest",
            "z_score",
            "local_outlier_factor",
            "one_class_svm",
            "autoencoder",
            "ewma",
            "cusum",
            "ensemble",
        })
        self.assertIn("no classification accuracy", LIMITATIONS)
        self.assertIn("assumes the configured segment is healthy", LIMITATIONS)
        validation_id = validation.id
        self.db.commit()
        self.db.expire_all()
        persisted = self.db.get(type(validation), validation_id)
        self.assertEqual(len(persisted.configuration["analyses"]), 36)
        self.assertIn("isolation_forest", persisted.configuration["detector_settings"])

    def test_non_nasa_dataset_is_rejected(self) -> None:
        self.dataset.file_format = "csv"
        with self.assertRaisesRegex(ValueError, "only for NASA IMS"):
            create_nasa_ims_validation(
                self.db,
                dataset_id=self.dataset.id,
                healthy_period_fraction=0.2,
                sustained_alert_windows=5,
                time_bin_count=100,
                created_by=1,
            )

    def test_second_active_validation_for_dataset_is_rejected(self) -> None:
        self.db.add(
            NasaIMSValidation(
                dataset_id=self.dataset.id,
                status="queued",
                configuration={},
                analysis_run_ids=[],
                result={},
                limitations=LIMITATIONS,
            )
        )
        self.db.flush()
        with self.assertRaises(NasaIMSValidationInProgress):
            create_nasa_ims_validation(
                self.db,
                dataset_id=self.dataset.id,
                healthy_period_fraction=0.2,
                sustained_alert_windows=5,
                time_bin_count=100,
                created_by=1,
            )


class NasaIMSValidationRBACTests(unittest.TestCase):
    def test_analyst_can_queue_validation_and_action_is_audited(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/evaluations")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=5, role="data_analyst"
        )
        db = Mock()
        app.dependency_overrides[get_db] = lambda: db
        validation = SimpleNamespace(
            id=22,
            dataset_id=7,
            created_by=5,
            status="queued",
            configuration={"test_set": "2nd_test"},
            analysis_run_ids=list(range(32)),
            limitations=LIMITATIONS,
            error_message=None,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            started_at=None,
            completed_at=None,
        )
        with (
            patch(
                "app.modules.datasets.api.nasa_ims_validation.create_nasa_ims_validation",
                return_value=validation,
            ),
            patch(
                "app.modules.datasets.api.nasa_ims_validation.run_nasa_ims_validation"
            ) as worker,
            TestClient(app) as client,
        ):
            response = client.post(
                "/evaluations",
                json={
                    "dataset_id": 7,
                    "healthy_period_fraction": 0.2,
                    "sustained_alert_windows": 5,
                    "time_bin_count": 100,
                },
            )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status"], "queued")
        db.add.assert_called_once()
        db.commit.assert_called_once()
        worker.assert_called_once_with(22)

    def test_technician_can_read_verified_facts(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/evaluations")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=5, role="maintenance_technician"
        )
        db = Mock()
        db.scalars.return_value = []
        app.dependency_overrides[get_db] = lambda: db

        with TestClient(app) as client:
            response = client.get("/evaluations/facts?test_set=2nd_test")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])

    def test_completed_validation_csv_export_is_audited(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/evaluations")
        user = SimpleNamespace(id=5, role="data_analyst")
        app.dependency_overrides[get_current_user] = lambda: user
        validation = SimpleNamespace(id=22, status="completed")
        db = Mock()
        db.get.return_value = validation
        app.dependency_overrides[get_db] = lambda: db

        with (
            patch(
                "app.modules.datasets.api.nasa_ims_validation.nasa_ims_validation_to_csv",
                return_value="record_type,test_set\nbearing_summary,2nd_test\n",
            ),
            TestClient(app) as client,
        ):
            response = client.get("/evaluations/22/csv")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.text, "record_type,test_set\nbearing_summary,2nd_test\n")
        self.assertEqual(response.headers["content-type"], "text/csv; charset=utf-8")
        self.assertEqual(db.add.call_args.args[0].action, "export")
        self.assertEqual(db.add.call_args.args[0].entity_id, 22)
        db.commit.assert_called_once()

    def test_completed_validation_pdf_export_is_audited(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/evaluations")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=5, role="data_analyst"
        )
        db = Mock()
        db.get.return_value = SimpleNamespace(
            id=22,
            status="completed",
            configuration={"test_set": "2nd_test"},
            limitations="Stored limitation",
        )
        app.dependency_overrides[get_db] = lambda: db

        with (
            patch(
                "app.modules.datasets.api.nasa_ims_validation._validation_pdf",
                return_value=b"%PDF-1.4 test",
            ),
            TestClient(app) as client,
        ):
            response = client.get("/evaluations/22/pdf")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("application/pdf", response.headers["content-type"])
        self.assertEqual(db.add.call_args.args[0].action, "export")
        db.commit.assert_called_once()

    def test_incomplete_validation_csv_export_is_rejected(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/evaluations")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=5, role="data_analyst"
        )
        db = Mock()
        db.get.return_value = SimpleNamespace(id=22, status="running")
        app.dependency_overrides[get_db] = lambda: db

        with TestClient(app) as client:
            response = client.get("/evaluations/22/csv")

        self.assertEqual(response.status_code, 409)
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_technician_cannot_start_validation(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/evaluations")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=5, role="maintenance_technician"
        )
        app.dependency_overrides[get_db] = lambda: Mock()

        with TestClient(app) as client:
            response = client.post(
                "/evaluations",
                json={
                    "dataset_id": 7,
                    "healthy_period_fraction": 0.2,
                    "sustained_alert_windows": 5,
                    "time_bin_count": 100,
                },
            )

        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
