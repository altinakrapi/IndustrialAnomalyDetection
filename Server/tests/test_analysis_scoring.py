import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from inspect import signature
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import joblib
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.core.notifications import NOTIFICATION_INSERT_BATCH_SIZE, notify_users_batch
from app.modules.anomaly_detection.api.router import (
    _anomaly_query,
    anomaly_payload,
    anomaly_summary,
    export_anomalies,
    list_anomalies,
    page_anomalies,
)
from app.modules.datasets.application.nasa_ims_model import (
    FEATURE_NAMES,
    MODEL_VERSION,
    _training_pipeline,
    is_compatible_nasa_ims_schema,
    is_compatible_nasa_ims_observation,
    predict_nasa_ims_matrix,
)
from app.modules.devices.application.analysis import (
    _compatible_nasa_ims_features,
    analyze_equipment_measurement,
)
from app.shared.feature_selection import numeric_analysis_features
from app.shared.database.base import Base
from app.shared.models import (
    AlertEpisode,
    AnalysisResult,
    AnalysisRun,
    Anomaly,
    Dataset,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
)
from app.workers.analysis_worker import (
    _inclusive_score_percentiles,
    _multiple_testing_adjusted_threshold,
    _robust_z_scores,
)


class AnalysisFeatureSelectionTests(unittest.TestCase):
    def test_nasa_metadata_columns_are_not_analysis_features(self) -> None:
        metadata = [
            {"name": "window_index", "type": "numeric"},
            {"name": "sample_start", "type": "numeric"},
            {"name": "sample_end", "type": "numeric"},
            {"name": "sensor_1_rms", "type": "numeric"},
            {"name": "source_file", "type": "text"},
        ]

        self.assertEqual(
            numeric_analysis_features("nasa_ims", metadata),
            ["sensor_1_rms"],
        )

    def test_same_named_fields_remain_available_for_non_nasa_data(self) -> None:
        metadata = [{"name": "sample_start", "type": "numeric"}]

        self.assertEqual(
            numeric_analysis_features("csv", metadata),
            ["sample_start"],
        )


class NotificationBatchTests(unittest.TestCase):
    def test_analysis_notification_batches_are_chunked_and_idempotent(self) -> None:
        database = Mock()
        notifications = [
            {
                "user_id": 1,
                "title": f"Anomaly {index}",
                "message": "Statistical evidence for human review",
                "notification_type": "anomaly_detected",
                "details": {},
                "dedupe_key": f"anomaly:{index}",
                "is_read": False,
            }
            for index in range(NOTIFICATION_INSERT_BATCH_SIZE + 1)
        ]

        notify_users_batch(database, notifications)

        self.assertEqual(database.execute.call_count, 2)
        first_statement = database.execute.call_args_list[0].args[0]
        self.assertIn(
            "ON CONFLICT (user_id, dedupe_key) DO NOTHING",
            str(first_statement.compile(dialect=postgresql.dialect())),
        )


class ZScoreScoringTests(unittest.TestCase):
    def test_vectorized_score_percentiles_match_legacy_inclusive_ranks(self) -> None:
        scores = np.array([0.1, 0.5, 0.5, 0.9, np.nan])

        actual = _inclusive_score_percentiles(scores)
        expected = np.array(
            [np.mean(scores <= score) for score in scores],
            dtype=np.float64,
        )

        np.testing.assert_array_equal(actual, expected)

    def test_multiple_testing_correction_scales_with_feature_count(self) -> None:
        self.assertEqual(_multiple_testing_adjusted_threshold(3.0, 1), 3.0)
        self.assertGreater(_multiple_testing_adjusted_threshold(3.0, 72), 3.0)

    def test_robust_z_score_is_not_diluted_by_an_extreme_baseline_outlier(self) -> None:
        values = np.array([[-2.0], [-1.0], [0.0], [1.0], [2.0], [50.0]])

        robust_score = _robust_z_scores(values)[-1, 0]
        mean_standard_deviation_score = (
            values[-1, 0] - values[:, 0].mean()
        ) / values[:, 0].std()

        self.assertGreater(robust_score, mean_standard_deviation_score)


class NasaImsPersistentModelTests(unittest.TestCase):
    def test_pretrained_schema_is_limited_to_exact_nasa_bearing_features(self) -> None:
        self.assertTrue(
            is_compatible_nasa_ims_schema("nasa_ims", FEATURE_NAMES)
        )
        self.assertFalse(
            is_compatible_nasa_ims_schema("csv", FEATURE_NAMES)
        )
        self.assertFalse(
            is_compatible_nasa_ims_schema("nasa_ims", ("temperature",))
        )
        self.assertFalse(
            is_compatible_nasa_ims_schema("nasa_ims", FEATURE_NAMES[::-1])
        )

    def test_saved_model_loads_and_predicts_with_its_fitted_preprocessing(self) -> None:
        rng = np.random.default_rng(42)
        training = rng.normal(size=(256, len(FEATURE_NAMES)))
        test = np.vstack(
            [
                rng.normal(size=(4, len(FEATURE_NAMES))),
                np.full((1, len(FEATURE_NAMES)), 20.0),
            ]
        )
        pipeline = _training_pipeline(len(training))
        pipeline.fit(training)
        artifact = {
            "model_version": MODEL_VERSION,
            "features": list(FEATURE_NAMES),
            "metadata": {"training_rows": len(training)},
            "pipeline": pipeline,
        }

        with tempfile.TemporaryDirectory() as directory:
            artifact_path = Path(directory) / "trained-model.joblib"
            joblib.dump(artifact, artifact_path)
            with patch(
                "app.modules.datasets.application.nasa_ims_model.model_artifact_path",
                return_value=artifact_path,
            ):
                predictions, scores, threshold, metadata = predict_nasa_ims_matrix(
                    test
                )

        self.assertEqual(predictions.shape, (5,))
        self.assertEqual(scores.shape, (5,))
        self.assertTrue(np.isfinite(scores).all())
        self.assertTrue(np.isfinite(threshold))
        self.assertEqual(metadata["training_rows"], len(training))
        self.assertEqual(predictions[-1], -1)

    def test_equipment_model_requires_complete_finite_feature_vector(self) -> None:
        complete = {name: float(index) for index, name in enumerate(FEATURE_NAMES)}

        self.assertTrue(is_compatible_nasa_ims_observation(complete))
        self.assertFalse(
            is_compatible_nasa_ims_observation(
                {"temperature": 42.0}
            )
        )
        incomplete = complete.copy()
        incomplete.pop(FEATURE_NAMES[-1])
        self.assertFalse(is_compatible_nasa_ims_observation(incomplete))
        non_finite = complete.copy()
        non_finite[FEATURE_NAMES[0]] = float("nan")
        self.assertFalse(is_compatible_nasa_ims_observation(non_finite))

    def test_equipment_vector_accepts_registered_manual_and_simulated_values(self) -> None:
        measured_at = "2026-10-26T12:00:00+00:00"
        features = {name: float(index) for index, name in enumerate(FEATURE_NAMES)}
        database = Mock()
        database.execute.return_value.all.return_value = list(features.items())
        measurement = Mock()
        measurement.parameter.device.id = 7
        measurement.parameter.device.is_registered = True
        measurement.measured_at = measured_at
        measurement.source = "manual"
        measurement.source_type = "manual"

        result = _compatible_nasa_ims_features(database, measurement)

        self.assertEqual(tuple(result or {}), FEATURE_NAMES)
        self.assertEqual(result, features)

        measurement.source = "synthetic_test"
        measurement.source_type = "simulated"
        self.assertEqual(
            _compatible_nasa_ims_features(database, measurement),
            features,
        )
        measurement.source = "manual"
        measurement.source_type = "manual"
        database.execute.return_value.all.return_value = list(features.items())[:-1]
        self.assertIsNone(_compatible_nasa_ims_features(database, measurement))

        measurement.parameter.device.is_registered = False
        self.assertIsNone(_compatible_nasa_ims_features(database, measurement))

    @patch("app.modules.devices.application.analysis.evaluate_alert_state")
    @patch("app.modules.devices.application.analysis.notify_user")
    @patch("app.modules.devices.application.analysis.notify_roles")
    @patch("app.modules.devices.application.analysis.predict_nasa_ims_matrix")
    @patch("app.modules.devices.application.analysis.model_is_available", return_value=True)
    def test_equipment_analysis_uses_pretrained_pipeline_for_compatible_vector(
        self,
        _model_available: Mock,
        predict: Mock,
        _notify_roles: Mock,
        _notify_user: Mock,
        evaluate_alert_state: Mock,
    ) -> None:
        measured_at = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        features = {name: float(index) for index, name in enumerate(FEATURE_NAMES)}
        database = Mock()
        database.execute.return_value.all.return_value = list(features.items())
        database.scalars.return_value = [float(index) for index in range(20)]

        def persist_id(record: object) -> None:
            if isinstance(record, AnalysisRun):
                record.id = 41
            elif isinstance(record, Anomaly):
                record.id = 52

        database.add.side_effect = persist_id
        parameter = SimpleNamespace(
            id=3,
            name=FEATURE_NAMES[0],
            unit="feature",
            analysis_method="isolation_forest",
            lower_limit=None,
            upper_limit=None,
            warning_low=None,
            warning_high=None,
            critical_low=None,
            critical_high=None,
            z_score_threshold=3.0,
            isolation_contamination=0.05,
            expected_interval_minutes=120,
            consecutive_anomaly_threshold=None,
            persistence_window_size=None,
            persistence_anomaly_threshold=None,
            updated_at=measured_at,
            device=SimpleNamespace(
                id=7,
                name="Bearing rig",
                device_code="BR-7",
                is_registered=True,
                responsible_user_id=9,
            ),
        )
        measurement = SimpleNamespace(
            id=12,
            parameter=parameter,
            value=features[FEATURE_NAMES[0]],
            unit="feature",
            measured_at=measured_at,
            recorded_by=9,
            source="manual",
            source_type="manual",
        )
        predict.return_value = (
            np.asarray([-1]),
            np.asarray([0.8]),
            0.5,
            {
                "name": "NASA IMS Bearings Isolation Forest",
                "model_version": 1,
                "training_dataset_id": 2,
                "training_dataset_name": "2nd_test",
                "preprocessing": {"imputation": "median", "scaling": "standard"},
            },
        )
        evaluate_alert_state.return_value = SimpleNamespace(
            history_state="ready",
            history_size=20,
            history_required=20,
            consecutive_anomalies=1,
            window_anomalies=1,
            priority="watch",
            alert_rules=["model"],
            episode=SimpleNamespace(id=13, status="open"),
            episode_created=True,
        )

        run = analyze_equipment_measurement(database, measurement)

        self.assertEqual(run.status, "completed")
        self.assertEqual(run.anomalies_found, 1)
        self.assertEqual(
            run.metrics["configuration"]["pretrained_model"]["name"],
            "NASA IMS Bearings Isolation Forest",
        )
        model_input = predict.call_args.args[0]
        self.assertEqual(model_input.shape, (1, len(FEATURE_NAMES)))
        self.assertEqual(
            model_input.tolist()[0],
            [features[name] for name in FEATURE_NAMES],
        )
        stored_results = [
            call.args[0]
            for call in database.add.call_args_list
            if isinstance(call.args[0], AnalysisResult)
        ]
        self.assertEqual(len(stored_results), 1)
        self.assertTrue(stored_results[0].is_anomaly)
        self.assertEqual(
            stored_results[0].feature_values[FEATURE_NAMES[-1]],
            features[FEATURE_NAMES[-1]],
        )
        _model_available.assert_called_once_with()
        _notify_roles.assert_called_once()
        _notify_user.assert_called_once()


class EquipmentEnsembleAnalysisTests(unittest.TestCase):
    def _measurement(self, method: str, value: float) -> tuple[Mock, SimpleNamespace]:
        measured_at = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        database = Mock()
        database.scalars.return_value = np.random.default_rng(42).normal(
            0, 0.2, 25
        ).tolist()
        parameter = SimpleNamespace(
            id=3,
            device_id=7,
            name="vibration",
            unit="mm/s",
            analysis_method=method,
            lower_limit=None,
            upper_limit=None,
            warning_low=None,
            warning_high=None,
            critical_low=None,
            critical_high=None,
            z_score_threshold=3.0,
            isolation_contamination=0.05,
            expected_interval_minutes=120,
            consecutive_anomaly_threshold=None,
            persistence_window_size=None,
            persistence_anomaly_threshold=None,
            updated_at=measured_at,
            device=SimpleNamespace(
                id=7,
                name="Rig",
                device_code="RIG-7",
                is_registered=True,
                responsible_user_id=None,
            ),
        )
        measurement = SimpleNamespace(
            id=12,
            parameter=parameter,
            value=value,
            unit="mm/s",
            measured_at=measured_at,
            recorded_by=None,
            source_type="manual",
        )
        return database, measurement

    @patch("app.modules.devices.application.analysis.notify_user")
    @patch("app.modules.devices.application.analysis.notify_roles")
    @patch("app.modules.devices.application.analysis.evaluate_alert_state")
    def test_equipment_ensemble_votes_before_layered_persistence(
        self,
        evaluate_alert_state: Mock,
        _notify_roles: Mock,
        _notify_user: Mock,
    ) -> None:
        database, measurement = self._measurement("ensemble", 8.0)
        evaluate_alert_state.return_value = SimpleNamespace(
            history_state="ready",
            history_size=25,
            history_required=20,
            consecutive_anomalies=1,
            window_anomalies=1,
            priority="watch",
            alert_rules=["model"],
            episode=SimpleNamespace(id=13, status="open"),
            episode_created=False,
        )

        run = analyze_equipment_measurement(database, measurement)

        stored_result = next(
            call.args[0]
            for call in database.add.call_args_list
            if isinstance(call.args[0], AnalysisResult)
        )
        self.assertEqual(run.metrics["configuration"]["algorithm"], "ensemble")
        self.assertTrue(stored_result.is_anomaly)
        self.assertEqual(stored_result.history_state, "ready")
        self.assertEqual(stored_result.result_details["fired_rule"], "majority vote")
        self.assertEqual(len(stored_result.result_details["detectors"]), 2)
        self.assertTrue(
            all(member["vote"] is True for member in stored_result.result_details["detectors"])
        )
        evaluate_alert_state.assert_called_once()
        self.assertTrue(evaluate_alert_state.call_args.kwargs["is_anomalous"])

    @patch("app.modules.devices.application.analysis.notify_user")
    @patch("app.modules.devices.application.analysis.notify_roles")
    @patch("app.modules.devices.application.analysis.evaluate_alert_state")
    def test_equipment_ensemble_does_not_vote_before_member_quorum(
        self,
        evaluate_alert_state: Mock,
        _notify_roles: Mock,
        _notify_user: Mock,
    ) -> None:
        database, measurement = self._measurement("ensemble", 8.0)
        database.scalars.return_value = np.random.default_rng(42).normal(
            0, 0.2, 10
        ).tolist()
        evaluate_alert_state.return_value = SimpleNamespace(
            history_state="insufficient_history",
            history_size=10,
            history_required=20,
            consecutive_anomalies=0,
            window_anomalies=0,
            priority=None,
            alert_rules=[],
            episode=None,
            episode_created=False,
        )

        run = analyze_equipment_measurement(database, measurement)

        stored_result = next(
            call.args[0]
            for call in database.add.call_args_list
            if isinstance(call.args[0], AnalysisResult)
        )
        self.assertEqual(run.anomalies_found, 0)
        self.assertEqual(stored_result.history_state, "insufficient_history")
        self.assertEqual(stored_result.result_details["ensemble_state"], "insufficient_history")
        self.assertFalse(evaluate_alert_state.call_args.kwargs["is_anomalous"])

    @patch("app.modules.devices.application.analysis.notify_user")
    @patch("app.modules.devices.application.analysis.notify_roles")
    def test_ensemble_anomaly_enters_layered_persistence_escalation(
        self, _notify_roles: Mock, _notify_user: Mock
    ) -> None:
        database, first_measurement = self._measurement("ensemble", 8.0)
        first_measurement.parameter.consecutive_anomaly_threshold = 2
        open_episode: AlertEpisode | None = None
        next_id = 40
        database.get.return_value = None

        def capture_add(record: object) -> None:
            nonlocal open_episode, next_id
            if isinstance(record, AlertEpisode):
                next_id += 1
                record.id = next_id
                open_episode = record
            elif isinstance(record, (AnalysisRun, Anomaly)):
                next_id += 1
                record.id = next_id

        def scalar(statement: object) -> AlertEpisode | None:
            return open_episode if "alert_episodes" in str(statement) else None

        database.add.side_effect = capture_add
        database.scalar.side_effect = scalar
        database.execute.side_effect = [[], [(True, first_measurement.id)]]

        first_run = analyze_equipment_measurement(database, first_measurement)
        second_measurement_values = vars(first_measurement).copy()
        second_measurement_values.update({
            "id": first_measurement.id + 1,
            "measured_at": first_measurement.measured_at + timedelta(minutes=1),
        })
        second_measurement = SimpleNamespace(**second_measurement_values)
        second_run = analyze_equipment_measurement(database, second_measurement)
        anomalies = [
            call.args[0]
            for call in database.add.call_args_list
            if isinstance(call.args[0], Anomaly)
        ]

        self.assertEqual(first_run.anomalies_found, 1)
        self.assertEqual(second_run.anomalies_found, 1)
        self.assertEqual(anomalies[0].alert_priority, "watch")
        self.assertEqual(anomalies[1].alert_priority, "elevated")
        self.assertIn("persistence", anomalies[1].alert_rules)


class EquipmentAnomalyPageDefaultTests(unittest.TestCase):
    def test_payload_includes_trained_model_and_inspection_assignee(self) -> None:
        measurement = SimpleNamespace(
            id=17,
            source="manual",
            source_type="manual",
            parameter=SimpleNamespace(id=5, name="Vibration RMS"),
            value=8.1,
            unit="mm/s",
            measured_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        order = SimpleNamespace(
            id=21,
            title="Inspect bearing",
            status="assigned",
            priority="high",
            assigned_to=8,
            assignee=SimpleNamespace(full_name="Morgan Technician"),
            due_date=None,
            resolution_notes=None,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        analysis = SimpleNamespace(
            id=12,
            status="completed",
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            dataset=None,
            device=SimpleNamespace(
                id=3,
                name="Bearing Test Rig",
                device_code="BR-3",
                is_registered=True,
            ),
            model_config=None,
            model_config_id=None,
            metrics={
                "configuration": {
                    "algorithm": "isolation_forest",
                    "pretrained_model": {
                        "name": "NASA IMS Bearings Isolation Forest"
                    },
                }
            },
            source_type="equipment",
            execution_type="automatic",
        )
        anomaly = SimpleNamespace(
            id=9,
            analysis_run_id=12,
            analysis_run=analysis,
            equipment_measurement=measurement,
            equipment_measurement_id=17,
            measurement_record_id=None,
            measurement_record=None,
            anomaly_type="isolation_forest",
            anomaly_score=-0.2,
            severity="high",
            status="new",
            feature_values={"vibration_rms": 8.1},
            expected_range={},
            description="Vibration RMS is outside the learned baseline.",
            detected_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            reviews=[],
            maintenance_orders=[order],
        )

        payload = anomaly_payload(anomaly)

        self.assertEqual(payload["model_name"], "NASA IMS Bearings Isolation Forest")
        self.assertEqual(
            payload["maintenance_orders"][0]["assigned_name"],
            "Morgan Technician",
        )

    def test_anomaly_page_endpoints_default_to_equipment_source(self) -> None:
        for endpoint in (
            list_anomalies,
            anomaly_summary,
            page_anomalies,
            export_anomalies,
        ):
            with self.subTest(endpoint=endpoint.__name__):
                self.assertEqual(
                    signature(endpoint).parameters["source_type"].default,
                    "equipment",
                )
        query = _anomaly_query()
        rendered_query = str(query.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("analysis_runs.source_type = 'equipment'", rendered_query)
        self.assertIn("anomalies.equipment_measurement_id IS NOT NULL", rendered_query)
        self.assertIn(
            "equipment_measurements.source_type IN ('manual', 'sensor')",
            rendered_query,
        )

    def test_test_fixture_anomalies_require_explicit_opt_in(self) -> None:
        for endpoint in (
            list_anomalies,
            anomaly_summary,
            page_anomalies,
            export_anomalies,
        ):
            with self.subTest(endpoint=endpoint.__name__):
                self.assertFalse(
                    signature(endpoint).parameters["include_test_data"].default
                )

        default_query = str(
            _anomaly_query().compile(compile_kwargs={"literal_binds": True})
        )
        test_query = str(
            _anomaly_query(include_test_data=True).compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        self.assertIn(
            "equipment_measurements.source_type IN ('manual', 'sensor')",
            default_query,
        )
        self.assertIn(
            "equipment_measurements.source_type IN ('manual', 'sensor', 'simulated')",
            test_query,
        )

    def test_anomaly_source_filter_includes_simulated_and_dataset_records(self) -> None:
        all_sources_query = str(
            _anomaly_query(source_type="all").compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        simulated_query = str(
            _anomaly_query(
                source_type="all",
                measurement_source_type="simulated",
            ).compile(compile_kwargs={"literal_binds": True})
        )
        dataset_query = str(
            _anomaly_query(
                source_type="all",
                measurement_source_type="dataset",
            ).compile(compile_kwargs={"literal_binds": True})
        )

        self.assertIn(
            "equipment_measurements.source_type IN ('manual', 'sensor', 'simulated')",
            all_sources_query,
        )
        self.assertIn("analysis_runs.source_type = 'dataset'", all_sources_query)
        self.assertIn(
            "equipment_measurements.source_type = 'simulated'",
            simulated_query,
        )
        self.assertIn("analysis_runs.source_type = 'dataset'", dataset_query)


class AnomalyRegisteredDeviceVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        device = Device(
            name="Simulated test motor",
            device_code="SIM-VISIBILITY-01",
            device_type="motor",
            source_type="simulated",
            is_registered=True,
        )
        self.db.add(device)
        self.db.flush()
        parameter = EquipmentParameter(
            device_id=device.id,
            name="Vibration RMS",
            unit="mm/s",
        )
        self.db.add(parameter)
        self.db.flush()
        measurement = EquipmentMeasurement(
            parameter_id=parameter.id,
            value=2.4,
            unit="mm/s",
            measured_at=datetime(2026, 10, 28, tzinfo=timezone.utc),
            source="simulated",
            source_type="simulated",
        )
        self.db.add(measurement)
        dataset = Dataset(
            name="Unlinked NASA dataset",
            file_path="nasa-ims.zip",
            file_format="nasa_ims",
        )
        self.db.add(dataset)
        self.db.flush()

        device_run = AnalysisRun(
            device_id=device.id,
            equipment_measurement_id=measurement.id,
            source_type="equipment",
            status="completed",
            metrics={},
        )
        dataset_run = AnalysisRun(
            dataset_id=dataset.id,
            source_type="dataset",
            status="completed",
            metrics={},
        )
        self.db.add_all([device_run, dataset_run])
        self.db.flush()
        self.db.add_all(
            [
                Anomaly(
                    analysis_run_id=device_run.id,
                    equipment_measurement_id=measurement.id,
                    anomaly_type="z_score",
                    severity="medium",
                    status="new",
                    feature_values={"Vibration RMS": 2.4},
                    expected_range={},
                ),
                Anomaly(
                    analysis_run_id=dataset_run.id,
                    anomaly_type="isolation_forest",
                    severity="high",
                    status="new",
                    feature_values={"bearing_1_rms": 4.2},
                    expected_range={},
                ),
            ]
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_page_hides_unlinked_dataset_anomalies_and_count_matches_items(self) -> None:
        response = page_anomalies(
            source_type="all",
            include_test_data=True,
            limit=50,
            offset=0,
            db=self.db,
        )

        self.assertEqual(response["total"], 1)
        self.assertEqual(len(response["items"]), 1)
        self.assertEqual(response["items"][0]["device_name"], "Simulated test motor")
        self.assertEqual(response["items"][0]["measurement_source_type"], "simulated")

    def test_include_dataset_shows_unlinked_dataset_anomalies_and_updates_count(self) -> None:
        response = page_anomalies(
            source_type="all",
            include_test_data=True,
            include_dataset=True,
            limit=50,
            offset=0,
            db=self.db,
        )

        self.assertEqual(response["total"], 2)
        self.assertEqual(len(response["items"]), 2)
        self.assertEqual(
            {item["algorithm"] for item in response["items"]},
            {"z_score", "isolation_forest"},
        )

    def test_summary_excludes_dataset_anomalies_by_default(self) -> None:
        summary = anomaly_summary(
            source_type="all",
            include_test_data=True,
            db=self.db,
        )

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["severity"]["medium"], 1)
        self.assertEqual(summary["severity"]["high"], 0)

    def test_summary_includes_dataset_anomalies_when_requested(self) -> None:
        summary = anomaly_summary(
            source_type="all",
            include_test_data=True,
            include_dataset=True,
            db=self.db,
        )

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["severity"]["medium"], 1)
        self.assertEqual(summary["severity"]["high"], 1)


if __name__ == "__main__":
    unittest.main()
