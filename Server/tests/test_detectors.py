import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM

from app.api.dependencies import get_current_user, get_db
from app.modules.analyses.api.router import router as analyses_router
from app.modules.analyses.domain.detectors import (
    DETECTOR_REGISTRY,
    AutoencoderDetector,
    CUSUMDetector,
    EWMADetector,
    EnsembleDetector,
    IsolationForestDetector,
    LocalOutlierFactorDetector,
    OneClassSVMDetector,
    ZScoreDetector,
    combine_ensemble_votes,
    detector_catalog,
    walk_forward_predict,
)
from app.shared.models import AnalysisComparison, AuditLog, ModelConfig
from app.workers.analysis_worker import (
    _persist_detector_artifact,
    _robust_z_scores,
    _score_z_score,
)


def _training_values() -> np.ndarray:
    return np.random.default_rng(42).normal(size=(80, 3))


class DetectorContractTests(unittest.TestCase):
    def test_registered_detectors_fit_score_predict_and_round_trip(self) -> None:
        values = _training_values()
        held_out = np.vstack([values[:5], np.full((1, values.shape[1]), 8.0)])
        configurations = {
            "isolation_forest": {"n_estimators": 30, "random_state": 11},
            "z_score": {"threshold": 3.0, "random_state": 11},
            "local_outlier_factor": {"n_neighbors": 10, "random_state": 11},
            "one_class_svm": {"nu": 0.05, "gamma": "scale", "kernel": "rbf"},
            "autoencoder": {
                "hidden_layer_sizes": [8, 4],
                "max_iter": 100,
                "random_state": 11,
            },
            "ewma": {"lambda": 0.2, "L": 3.0, "baseline_window": 30},
            "cusum": {"k": 0.5, "h": 5.0, "baseline_window": 30},
            "ensemble": {},
        }

        self.assertEqual(set(configurations), set(DETECTOR_REGISTRY))
        for algorithm, parameters in configurations.items():
            with self.subTest(algorithm=algorithm):
                detector = DETECTOR_REGISTRY[algorithm]()
                detector_values = values[:, :1] if algorithm in {"ewma", "cusum"} else values
                detector_held_out = held_out[:, :1] if algorithm in {"ewma", "cusum"} else held_out
                model = detector.fit(
                    detector_values,
                    parameters=parameters,
                    missing_values_strategy="median",
                    scaling="standard",
                )
                scores = detector.score(model, detector_held_out)
                predictions = detector.predict(model, detector_held_out)
                restored = detector.deserialize(detector.serialize(model))

                self.assertEqual(scores.shape, (6,))
                self.assertEqual(predictions.shape, (6,))
                self.assertTrue(np.isfinite(scores).all())
                np.testing.assert_array_equal(
                    detector.predict(restored, detector_held_out), predictions
                )
                np.testing.assert_allclose(
                    detector.score(restored, detector_held_out), scores
                )

    def test_detector_configuration_validation_rejects_invalid_values(self) -> None:
        invalid_configurations = (
            (IsolationForestDetector, {"n_estimators": 2}),
            (ZScoreDetector, {"threshold": float("nan")}),
            (LocalOutlierFactorDetector, {"n_neighbors": 1}),
            (LocalOutlierFactorDetector, {"refit_every": 0}),
            (LocalOutlierFactorDetector, {"n_neighbors": 20, "training_window": 20}),
            (OneClassSVMDetector, {"nu": 0.8}),
            (OneClassSVMDetector, {"gamma": "invalid"}),
            (OneClassSVMDetector, {"kernel": []}),
            (OneClassSVMDetector, {"refit_every": 0}),
            (AutoencoderDetector, {"hidden_layer_sizes": [2, 0]}),
            (AutoencoderDetector, {"max_iter": 1}),
            (AutoencoderDetector, {"refit_every": 0}),
            (EWMADetector, {"lambda": 0}),
            (EWMADetector, {"reset_rule": "never"}),
            (CUSUMDetector, {"h": float("inf")}),
            (CUSUMDetector, {"baseline_window": 7}),
        )
        for detector_class, parameters in invalid_configurations:
            with self.subTest(detector=detector_class.algorithm, parameters=parameters):
                with self.assertRaises(ValueError):
                    detector_class.validate_config(parameters)

    def test_same_configuration_and_seed_produce_deterministic_outputs(self) -> None:
        values = _training_values()
        held_out = np.vstack([values[:10], np.full((1, values.shape[1]), 6.0)])
        configurations = (
            (IsolationForestDetector(), {"n_estimators": 50, "random_state": 17}),
            (LocalOutlierFactorDetector(), {"n_neighbors": 12, "random_state": 17}),
            (OneClassSVMDetector(), {"nu": 0.07, "gamma": "scale", "kernel": "rbf"}),
        )
        for detector, parameters in configurations:
            with self.subTest(detector=detector.algorithm):
                models = [
                    detector.fit(
                        values,
                        parameters=parameters,
                        missing_values_strategy="median",
                        scaling="standard",
                    )
                    for _ in range(2)
                ]
                np.testing.assert_array_equal(
                    detector.predict(models[0], held_out),
                    detector.predict(models[1], held_out),
                )
                np.testing.assert_allclose(
                    detector.score(models[0], held_out),
                    detector.score(models[1], held_out),
                )

    def test_each_detector_exposes_its_minimum_history(self) -> None:
        self.assertEqual(IsolationForestDetector.minimum_history({}), 20)
        self.assertEqual(ZScoreDetector.minimum_history({}), 8)
        self.assertEqual(LocalOutlierFactorDetector.minimum_history({}), 21)
        self.assertEqual(
            LocalOutlierFactorDetector.minimum_history({"n_neighbors": 5}), 20
        )
        self.assertEqual(OneClassSVMDetector.minimum_history({}), 20)
        self.assertEqual(AutoencoderDetector.minimum_history({}), 30)
        self.assertEqual(EWMADetector.minimum_history({}), 30)
        self.assertEqual(CUSUMDetector.minimum_history({}), 30)

    def test_isolation_forest_and_z_score_preserve_legacy_scoring(self) -> None:
        values = _training_values()
        scaled = StandardScaler().fit_transform(values)
        legacy_forest = IsolationForest(
            n_estimators=100,
            contamination="auto",
            random_state=42,
            n_jobs=-1,
        ).fit(scaled)
        forest = IsolationForestDetector()
        forest_model = forest.fit(
            values,
            parameters={},
            missing_values_strategy="median",
            scaling="standard",
        )
        np.testing.assert_array_equal(
            forest.predict(forest_model, values),
            legacy_forest.predict(scaled),
        )
        np.testing.assert_allclose(
            forest.score(forest_model, values),
            -legacy_forest.score_samples(scaled),
            rtol=0,
            atol=1e-12,
        )

        legacy_z_scores = np.max(_robust_z_scores(values), axis=1)
        z_score = ZScoreDetector()
        z_score_model = z_score.fit(
            values,
            parameters={"threshold": 3.0},
            missing_values_strategy="median",
            scaling="none",
        )
        np.testing.assert_allclose(z_score.score(z_score_model, values), legacy_z_scores)
        np.testing.assert_array_equal(
            z_score.predict(z_score_model, values),
            np.where(legacy_z_scores > 3.0, -1, 1),
        )

    def test_isolation_forest_explicit_contamination_controls_normal_data_rate(self) -> None:
        normal = np.random.default_rng(2026).normal(size=(2000, 3))
        detector = IsolationForestDetector()
        model = detector.fit(
            normal,
            parameters={
                "n_estimators": 100,
                "contamination": 0.05,
                "random_state": 42,
            },
            missing_values_strategy="median",
            scaling="standard",
        )

        flagged_rate = float(np.mean(detector.predict(model, normal) == -1))

        self.assertGreaterEqual(flagged_rate, 0.04)
        self.assertLessEqual(flagged_rate, 0.06)

    def test_lof_and_svm_results_match_the_pre_registry_estimators(self) -> None:
        values = _training_values()
        held_out = np.vstack([values[:5], np.full((1, values.shape[1]), 8.0)])
        scaled = StandardScaler().fit(values).transform(values)
        scaled_held_out = StandardScaler().fit(values).transform(held_out)

        legacy_lof = Pipeline(
            [
                ("imputer", "passthrough"),
                ("scaler", StandardScaler()),
                (
                    "detector",
                    LocalOutlierFactor(n_neighbors=10, novelty=True, contamination="auto"),
                ),
            ]
        ).fit(values)
        lof = LocalOutlierFactorDetector()
        lof_model = lof.fit(
            values,
            parameters={"n_neighbors": 10},
            missing_values_strategy="drop",
            scaling="standard",
        )
        np.testing.assert_array_equal(
            lof.predict(lof_model, held_out), legacy_lof.predict(held_out)
        )
        np.testing.assert_allclose(
            lof.score(lof_model, held_out),
            -legacy_lof.named_steps["detector"].score_samples(
                legacy_lof[:-1].transform(held_out)
            ),
        )

        legacy_svm = OneClassSVM(nu=0.05, gamma="scale", kernel="rbf").fit(scaled)
        svm = OneClassSVMDetector()
        svm_model = svm.fit(
            values,
            parameters={"nu": 0.05, "gamma": "scale", "kernel": "rbf"},
            missing_values_strategy="drop",
            scaling="standard",
        )
        np.testing.assert_array_equal(
            svm.predict(svm_model, held_out), legacy_svm.predict(scaled_held_out)
        )
        np.testing.assert_allclose(
            svm.score(svm_model, held_out),
            -legacy_svm.decision_function(scaled_held_out),
        )

    def test_new_detectors_are_deterministic_with_the_same_seed_and_data(self) -> None:
        values = _training_values()
        held_out = np.vstack([values[:10], np.full((1, values.shape[1]), 7.0)])
        configurations = (
            (AutoencoderDetector(), {"hidden_layer_sizes": [8, 4], "max_iter": 120, "random_state": 42}, values, held_out),
            (EWMADetector(), {"lambda": 0.2, "L": 3.0, "baseline_window": 30}, values[:, :1], held_out[:, :1]),
            (CUSUMDetector(), {"k": 0.5, "h": 5.0, "baseline_window": 30}, values[:, :1], held_out[:, :1]),
        )
        for detector, parameters, training, scoring in configurations:
            with self.subTest(detector=detector.algorithm):
                first = detector.fit(
                    training,
                    parameters=parameters,
                    missing_values_strategy="median",
                    scaling="standard",
                )
                second = detector.fit(
                    training,
                    parameters=parameters,
                    missing_values_strategy="median",
                    scaling="standard",
                )
                np.testing.assert_array_equal(
                    detector.predict(first, scoring),
                    detector.predict(second, scoring),
                )
                np.testing.assert_allclose(
                    detector.score(first, scoring),
                    detector.score(second, scoring),
                )

    def test_autoencoder_reconstruction_error_is_higher_for_injected_outliers(self) -> None:
        rng = np.random.default_rng(2026)
        training = rng.normal(size=(500, 3))
        normal = rng.normal(size=(25, 3))
        outliers = np.full((5, 3), 8.0)
        detector = AutoencoderDetector()
        model = detector.fit(
            training,
            parameters={
                "hidden_layer_sizes": [12, 6],
                "max_iter": 250,
                "threshold_percentile": 99,
                "random_state": 42,
            },
            missing_values_strategy="median",
            scaling="standard",
        )
        self.assertGreater(
            float(np.mean(detector.score(model, outliers))),
            float(np.median(detector.score(model, normal))),
        )

    def test_autoencoder_scores_single_feature_vectors_without_broadcasting_rows(self) -> None:
        values = np.random.default_rng(2026).normal(size=(80, 1))
        detector = AutoencoderDetector()
        model = detector.fit(
            values[:40],
            parameters={"hidden_layer_sizes": [8, 4], "max_iter": 100, "random_state": 42},
            missing_values_strategy="median",
            scaling="standard",
        )

        scores = detector.score(model, values[40:])

        self.assertEqual(scores.shape, (40,))
        self.assertTrue(np.isfinite(scores).all())

    def test_ewma_and_cusum_detect_gradual_drift_and_level_shifts(self) -> None:
        rng = np.random.default_rng(18)
        baseline = rng.normal(0.0, 0.2, size=300)
        gradual = baseline.copy()
        gradual[100:] += np.linspace(0.0, 1.5, len(gradual) - 100)
        shifted = baseline.copy()
        shifted[100:] += 1.5
        configurations = (
            (EWMADetector(), {"lambda": 0.2, "L": 3.0, "baseline_window": 30}),
            (CUSUMDetector(), {"k": 0.5, "h": 5.0, "baseline_window": 30}),
        )
        for detector, parameters in configurations:
            for label, series in (("drift", gradual), ("level_shift", shifted)):
                with self.subTest(detector=detector.algorithm, signal=label):
                    result = walk_forward_predict(
                        detector,
                        series.reshape(-1, 1),
                        parameters=parameters,
                        missing_values_strategy="median",
                        scaling="none",
                        batch_size=25,
                    )
                    self.assertTrue(np.any(result.predictions == -1))
                    anomaly_index = int(np.flatnonzero(result.predictions == -1)[0])
                    self.assertIn(
                        result.result_details[anomaly_index]["shift_direction"],
                        {"upward", "downward"},
                    )
                    self.assertIsNotNone(
                        result.result_details[anomaly_index][
                            "shift_started_position"
                        ]
                    )

    def test_control_charts_stay_quiet_on_stationary_noise_and_document_stuck_signal(self) -> None:
        rng = np.random.default_rng(31)
        stationary = rng.normal(0.0, 0.2, size=500)
        stuck_at_baseline = np.concatenate(
            [stationary[:30], np.full(120, float(np.mean(stationary[:30])))]
        )
        configurations = (
            (EWMADetector(), {"lambda": 0.2, "L": 3.5, "baseline_window": 30}),
            (CUSUMDetector(), {"k": 0.5, "h": 7.0, "baseline_window": 30}),
        )
        for detector, parameters in configurations:
            with self.subTest(detector=detector.algorithm):
                noise_result = walk_forward_predict(
                    detector,
                    stationary.reshape(-1, 1),
                    parameters=parameters,
                    missing_values_strategy="median",
                    scaling="none",
                )
                stuck_result = walk_forward_predict(
                    detector,
                    stuck_at_baseline.reshape(-1, 1),
                    parameters=parameters,
                    missing_values_strategy="median",
                    scaling="none",
                )
                self.assertLessEqual(int(np.sum(noise_result.predictions == -1)), 4)
                self.assertFalse(np.any(stuck_result.predictions == -1))

    def test_ewma_signal_resets_state_and_reports_shift_start_on_first_signal(self) -> None:
        detector = EWMADetector()
        model = detector.fit(
            np.zeros((30, 1)),
            parameters={"lambda": 0.5, "L": 1.0, "baseline_window": 30},
            missing_values_strategy="median",
            scaling="none",
        )

        predictions = detector.predict(model, np.full((2, 1), 10.0))
        details = detector.result_details(model, np.full((2, 1), 10.0))

        self.assertEqual(predictions[0], -1)
        self.assertEqual(details[0]["shift_started_position"], 30)
        self.assertEqual(details[0]["reset_rule"], "signal")

    def test_walk_forward_new_detectors_do_not_leak_future_values(self) -> None:
        rng = np.random.default_rng(41)
        values = rng.normal(size=(240, 2))
        changed_future = values.copy()
        changed_future[140:] += 50
        configurations = (
            (AutoencoderDetector(), {"hidden_layer_sizes": [8, 4], "max_iter": 100, "random_state": 5}, values, changed_future),
            (EWMADetector(), {"lambda": 0.2, "L": 3.0, "baseline_window": 30}, values[:, :1], changed_future[:, :1]),
            (CUSUMDetector(), {"k": 0.5, "h": 5.0, "baseline_window": 30}, values[:, :1], changed_future[:, :1]),
        )
        for detector, parameters, original, changed in configurations:
            with self.subTest(detector=detector.algorithm):
                kwargs = {
                    "parameters": parameters,
                    "missing_values_strategy": "median",
                    "scaling": "standard",
                    "batch_size": 25,
                }
                baseline_result = walk_forward_predict(detector, original, **kwargs)
                changed_result = walk_forward_predict(detector, changed, **kwargs)
                before_change = baseline_result.positions < 140
                np.testing.assert_allclose(
                    baseline_result.scores[before_change],
                    changed_result.scores[before_change],
                )

    def test_5000_reading_simulated_device_walkforward_completes(self) -> None:
        values = np.random.default_rng(2026).normal(10.0, 0.25, size=(5000, 1))
        values[2500:] += np.linspace(0, 1, 2500).reshape(-1, 1)
        for detector, parameters in (
            (EWMADetector(), {"lambda": 0.2, "L": 3.0, "baseline_window": 30}),
            (CUSUMDetector(), {"k": 0.5, "h": 5.0, "baseline_window": 30}),
        ):
            with self.subTest(detector=detector.algorithm):
                started = time.perf_counter()
                result = walk_forward_predict(
                    detector,
                    values,
                    parameters=parameters,
                    missing_values_strategy="median",
                    scaling="standard",
                )
                self.assertEqual(len(result.positions), 4970)
                self.assertGreater(time.perf_counter() - started, 0)

    def test_walk_forward_scoring_never_uses_future_readings(self) -> None:
        detector = LocalOutlierFactorDetector()
        parameters = {"n_neighbors": 10}
        values = _training_values()
        changed_future = values.copy()
        changed_future[40:] += 100

        baseline = walk_forward_predict(
            detector,
            values,
            parameters=parameters,
            missing_values_strategy="median",
            scaling="standard",
            batch_size=20,
        )
        changed = walk_forward_predict(
            detector,
            changed_future,
            parameters=parameters,
            missing_values_strategy="median",
            scaling="standard",
            batch_size=20,
        )

        np.testing.assert_array_equal(baseline.positions[:20], np.arange(20, 40))
        np.testing.assert_allclose(baseline.scores[:20], changed.scores[:20])
        np.testing.assert_array_equal(
            baseline.predictions[:20], changed.predictions[:20]
        )

    def test_walk_forward_rejects_refit_cadence_larger_than_dataset(self) -> None:
        with self.assertRaisesRegex(ValueError, "smaller than the number of readings after"):
            walk_forward_predict(
                LocalOutlierFactorDetector(),
                _training_values(),
                parameters={"n_neighbors": 10, "refit_every": 100000},
                missing_values_strategy="median",
                scaling="standard",
            )

    def test_control_chart_walk_forward_matches_repeated_refit_outputs(self) -> None:
        rng = np.random.default_rng(20261028)
        values = rng.normal(0.0, 0.3, size=(240, 1))
        values[120:] += np.linspace(0.0, 1.5, 120).reshape(-1, 1)
        configurations = (
            (EWMADetector(), {"lambda": 0.2, "L": 3.0, "baseline_window": 30}),
            (CUSUMDetector(), {"k": 0.5, "h": 5.0, "baseline_window": 30}),
        )
        for detector, parameters in configurations:
            with self.subTest(detector=detector.algorithm):
                optimized = walk_forward_predict(
                    detector,
                    values,
                    parameters=parameters,
                    missing_values_strategy="median",
                    scaling="standard",
                    batch_size=25,
                )
                positions = np.arange(detector.minimum_history(parameters), len(values))
                predictions: list[int] = []
                scores: list[float] = []
                thresholds: list[float] = []
                severity_thresholds: list[tuple[float, float]] = []
                history_sizes: list[int] = []
                details: list[dict[str, object]] = []
                start = detector.minimum_history(parameters)
                while start < len(values):
                    end = min(start + 25, len(values))
                    history = values[:start]
                    model = detector.fit(
                        history,
                        parameters=parameters,
                        missing_values_strategy="median",
                        scaling="standard",
                    )
                    batch = values[start:end]
                    batch_predictions = detector.predict(model, batch)
                    batch_scores = detector.score(model, batch)
                    history_scores = detector.history_score_reference(model)
                    if not len(history_scores):
                        history_scores = detector.score(model, history)
                    severity_threshold = (
                        float(np.quantile(history_scores, 0.95)),
                        float(np.quantile(history_scores, 0.995)),
                    )
                    predictions.extend(int(item) for item in batch_predictions)
                    scores.extend(float(item) for item in batch_scores)
                    thresholds.extend(
                        [detector.decision_threshold(model)] * len(batch)
                    )
                    severity_thresholds.extend([severity_threshold] * len(batch))
                    history_sizes.extend([len(history)] * len(batch))
                    details.extend(detector.result_details(model, batch))
                    start = end

                np.testing.assert_array_equal(optimized.positions, positions)
                np.testing.assert_array_equal(optimized.predictions, predictions)
                np.testing.assert_allclose(optimized.scores, scores, rtol=0, atol=1e-12)
                np.testing.assert_allclose(
                    optimized.score_thresholds, thresholds, rtol=0, atol=1e-12
                )
                np.testing.assert_allclose(
                    optimized.severity_thresholds,
                    severity_thresholds,
                    rtol=0,
                    atol=1e-12,
                )
                np.testing.assert_array_equal(optimized.history_sizes, history_sizes)
                for actual_detail, expected_detail in zip(
                    optimized.result_details, details, strict=True
                ):
                    self.assertAlmostEqual(
                        actual_detail["baseline_mean"],
                        expected_detail["baseline_mean"],
                        places=12,
                    )
                    self.assertAlmostEqual(
                        actual_detail["baseline_standard_deviation"],
                        expected_detail["baseline_standard_deviation"],
                        places=12,
                    )
                    for key in expected_detail.keys() - {
                        "baseline_mean",
                        "baseline_standard_deviation",
                    }:
                        self.assertEqual(actual_detail[key], expected_detail[key])

    def test_worker_z_score_matches_detector_on_same_multivariate_matrix(self) -> None:
        values = np.random.default_rng(20261028).normal(size=(500, 36))
        values[::17, :3] += 5.0
        scores, predictions, effective_threshold = _score_z_score(
            values,
            threshold=3.0,
            missing_values_strategy="median",
        )
        detector = ZScoreDetector()
        model = detector.fit(
            values,
            parameters={"threshold": effective_threshold},
            missing_values_strategy="median",
            scaling="none",
        )

        np.testing.assert_allclose(scores, detector.score(model, values))
        np.testing.assert_array_equal(predictions, detector.predict(model, values))
        self.assertAlmostEqual(
            effective_threshold,
            3.9598580245337653,
            places=12,
        )

    def test_walk_forward_refits_at_configured_reading_cadence_without_future_leakage(self) -> None:
        values = _training_values()
        configurations = (
            (LocalOutlierFactorDetector(), {"n_neighbors": 10, "refit_every": 40}, 20),
            (
                OneClassSVMDetector(),
                {"nu": 0.05, "gamma": "scale", "kernel": "rbf", "refit_every": 40},
                20,
            ),
            (
                AutoencoderDetector(),
                {
                    "hidden_layer_sizes": [4, 2],
                    "max_iter": 50,
                    "random_state": 5,
                    "refit_every": 40,
                },
                30,
            ),
        )
        for detector, parameters, minimum_history in configurations:
            with self.subTest(detector=detector.algorithm):
                changed_future = values.copy()
                changed_future[minimum_history + 40 :] += 100
                kwargs = {
                    "parameters": parameters,
                    "missing_values_strategy": "median",
                    "scaling": "standard",
                    "batch_size": 256,
                }

                with patch.object(detector, "fit", wraps=detector.fit) as fit:
                    baseline = walk_forward_predict(detector, values, **kwargs)

                fit_history_sizes = [
                    len(call.args[0]) for call in fit.call_args_list
                ]
                self.assertEqual(
                    fit_history_sizes,
                    [minimum_history, minimum_history + 40],
                )
                first_batch = baseline.positions < minimum_history + 40
                changed = walk_forward_predict(detector, changed_future, **kwargs)
                np.testing.assert_allclose(
                    baseline.scores[first_batch],
                    changed.scores[first_batch],
                )

    def test_one_class_svm_walk_forward_handles_array_valued_decision_offset(self) -> None:
        result = walk_forward_predict(
            OneClassSVMDetector(),
            _training_values(),
            parameters={"nu": 0.05, "gamma": "scale", "kernel": "rbf"},
            missing_values_strategy="median",
            scaling="standard",
            batch_size=25,
        )

        self.assertEqual(len(result.positions), 60)
        self.assertTrue(np.isfinite(result.score_thresholds).all())

    def test_walk_forward_exposes_insufficient_history_without_scoring(self) -> None:
        result = walk_forward_predict(
            LocalOutlierFactorDetector(),
            _training_values()[:20],
            parameters={"n_neighbors": 20},
            missing_values_strategy="median",
            scaling="standard",
        )
        self.assertEqual(result.minimum_history, 21)
        self.assertEqual(len(result.positions), 0)
        self.assertIsNone(result.final_model)

    def test_detector_artifact_is_versioned_hashed_and_unique(self) -> None:
        detector = OneClassSVMDetector()
        model = detector.fit(
            _training_values(),
            parameters={},
            missing_values_strategy="median",
            scaling="standard",
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.workers.analysis_worker.settings.UPLOAD_DIR", directory):
                first = _persist_detector_artifact(
                    detector=detector,
                    model=model,
                    analysis_id=10,
                    parameters={"nu": 0.05},
                )
                second = _persist_detector_artifact(
                    detector=detector,
                    model=model,
                    analysis_id=10,
                    parameters={"nu": 0.05},
                )
            first_path = Path(directory) / first["path"]
            second_path = Path(directory) / second["path"]
            self.assertNotEqual(first_path, second_path)
            self.assertEqual(first["version"], detector.version())
            self.assertEqual(len(first["sha256"]), 64)
            self.assertTrue(first_path.is_file())
            self.assertTrue(second_path.is_file())
            self.assertEqual(
                detector.deserialize(first_path.read_bytes())["configuration"],
                {"nu": 0.05},
            )


class DetectorConfigurationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Mock()
        app = FastAPI()
        app.include_router(analyses_router, prefix="/analyses")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=3, role="data_analyst"
        )
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()

    def test_detector_catalog_is_registry_backed(self) -> None:
        response = self.client.get("/analyses/detectors")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {entry["algorithm"] for entry in response.json()},
            set(DETECTOR_REGISTRY),
        )
        self.assertEqual(response.json(), detector_catalog())

    def test_model_configuration_persists_validated_detector_parameters(self) -> None:
        response = self.client.post(
            "/analyses/models",
            json={
                "name": "LOF review",
                "algorithm": "lof",
                "detector_parameters": {"n_neighbors": 12, "random_state": 7},
            },
        )

        self.assertEqual(response.status_code, 201)
        saved = next(
            item for item in self.db.add.call_args_list
            if isinstance(item.args[0], ModelConfig)
        ).args[0]
        self.assertEqual(saved.algorithm, "local_outlier_factor")
        self.assertEqual(saved.parameters["n_neighbors"], 12)
        self.assertEqual(saved.parameters["random_state"], 7)

    def test_model_configuration_rejects_invalid_detector_parameters(self) -> None:
        response = self.client.post(
            "/analyses/models",
            json={
                "name": "Invalid LOF",
                "algorithm": "local_outlier_factor",
                "detector_parameters": {"n_neighbors": 1},
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_model_configuration_preserves_role_permissions(self) -> None:
        app = FastAPI()
        app.include_router(analyses_router, prefix="/analyses")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=4, role="maintenance_technician"
        )
        app.dependency_overrides[get_db] = lambda: self.db
        with TestClient(app) as client:
            response = client.post(
                "/analyses/models",
                json={"name": "Unauthorized", "algorithm": "one_class_svm"},
            )
        self.assertEqual(response.status_code, 403)

    def test_ensemble_configuration_is_available_through_registry_api(self) -> None:
        response = self.client.post(
            "/analyses/models",
            json={
                "name": "Ensemble review",
                "algorithm": "ensemble",
                "detector_parameters": {
                    "members": [
                        {"algorithm": "isolation_forest", "features": ["x"]},
                        {"algorithm": "z_score", "features": ["x"]},
                    ],
                    "voting_mode": "majority",
                },
            },
        )

        self.assertEqual(response.status_code, 201)
        saved = next(
            item for item in self.db.add.call_args_list
            if isinstance(item.args[0], ModelConfig)
        ).args[0]
        self.assertEqual(saved.algorithm, "ensemble")
        self.assertEqual(len(saved.parameters["members"]), 2)

    def test_ensemble_configuration_rejects_incompatible_chart_features(self) -> None:
        response = self.client.post(
            "/analyses/models",
            json={
                "name": "Invalid ensemble",
                "algorithm": "ensemble",
                "detector_parameters": {
                    "members": [
                        {
                            "algorithm": "ewma",
                            "features": ["x", "y"],
                        },
                        {"algorithm": "z_score", "features": ["x"]},
                    ],
                },
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_analysis_result_api_exposes_insufficient_history_state(self) -> None:
        record = SimpleNamespace(
            id=5,
            measurement_record_id=None,
            equipment_measurement_id=None,
            source_row_number=22,
            is_anomaly=False,
            anomaly_score=None,
            history_state="insufficient_history",
            history_size=20,
            history_required=21,
            result_details={},
            feature_values={"sensor_1_rms": 0.1},
        )
        self.db.get.return_value = SimpleNamespace(id=9)
        self.db.scalars.side_effect = [[record], [], []]
        self.db.scalar.return_value = 1

        response = self.client.get("/analyses/9/results")

        self.assertEqual(response.status_code, 200)
        result = response.json()["results"][0]
        self.assertEqual(result["history_state"], "insufficient_history")
        self.assertEqual(result["history_size"], 20)
        self.assertEqual(result["history_required"], 21)


class EnsembleDetectorTests(unittest.TestCase):
    def test_two_member_majority_known_answers_on_synthetic_windows(self) -> None:
        configuration = {
            "quorum": 2,
            "voting_mode": "majority",
            "high_confidence_threshold": 0.75,
        }
        member_votes = [
            [{"vote": True, "weight": 1.0}, {"vote": True, "weight": 1.0}],
            [{"vote": True, "weight": 1.0}, {"vote": False, "weight": 1.0}],
            [{"vote": False, "weight": 1.0}, {"vote": False, "weight": 1.0}],
        ]
        expected = [True, False, False]

        actual = [
            combine_ensemble_votes(votes, configuration)[0]
            for votes in member_votes
        ]

        self.assertEqual(actual, expected)

    def test_majority_weighted_and_quorum_voting(self) -> None:
        votes = [
            {"vote": True, "weight": 1.0},
            {"vote": True, "weight": 1.0},
            {"vote": False, "weight": 1.0},
        ]
        config = {
            "quorum": 2,
            "voting_mode": "majority",
            "high_confidence_threshold": 0.75,
        }
        self.assertEqual(
            combine_ensemble_votes(votes, config),
            (True, 2 / 3, "ready", False),
        )
        weighted = {
            **config,
            "voting_mode": "weighted",
        }
        weighted_votes = [
            {"vote": True, "weight": 4.0},
            {"vote": False, "weight": 1.0},
            {"vote": False, "weight": 1.0},
        ]
        self.assertEqual(
            combine_ensemble_votes(weighted_votes, weighted),
            (True, 4 / 6, "ready", False),
        )
        quorum = {**config, "voting_mode": "quorum"}
        self.assertEqual(
            combine_ensemble_votes(votes, quorum),
            (True, 2 / 3, "ready", False),
        )
        self.assertEqual(
            combine_ensemble_votes(
                [
                    {"vote": True, "weight": 1.0},
                    {"vote": False, "weight": 1.0},
                    {"vote": False, "weight": 1.0},
                ],
                config,
            ),
            (False, 2 / 3, "ready", False),
        )

    def test_insufficient_members_do_not_vote_and_confidence_tracks_agreement(self) -> None:
        config = {
            "quorum": 2,
            "voting_mode": "majority",
            "high_confidence_threshold": 0.75,
        }
        self.assertEqual(
            combine_ensemble_votes([{"vote": True, "weight": 1.0}], config),
            (None, 0.0, "insufficient_history", False),
        )
        one_agreeing = [
            {"vote": True, "weight": 1.0},
            {"vote": False, "weight": 1.0},
        ]
        three_agreeing = [
            {"vote": True, "weight": 1.0},
            {"vote": True, "weight": 1.0},
            {"vote": True, "weight": 1.0},
        ]
        one_confidence = combine_ensemble_votes(one_agreeing, config)[1]
        three_confidence = combine_ensemble_votes(three_agreeing, config)[1]
        self.assertGreater(three_confidence, one_confidence)
        self.assertTrue(combine_ensemble_votes(three_agreeing, config)[3])
        weighted_single_vote = combine_ensemble_votes(
            [
                {"vote": True, "weight": 10.0},
                {"vote": False, "weight": 1.0},
            ],
            {**config, "voting_mode": "weighted"},
        )
        self.assertGreater(weighted_single_vote[1], 0.9)
        self.assertFalse(weighted_single_vote[3])

    def test_member_below_minimum_history_is_non_voting(self) -> None:
        detector = EnsembleDetector()
        configuration = detector.validate_config({
            "members": [
                {"algorithm": "isolation_forest", "features": [0]},
                {"algorithm": "z_score", "features": [0]},
            ],
        })
        self.assertEqual(detector.minimum_history(configuration), 8)
        history = np.arange(10, dtype=np.float64).reshape(-1, 1)
        fitted = detector.fit(
            history,
            parameters=configuration,
            missing_values_strategy="median",
            scaling="standard",
        )
        details = detector.result_details(
            fitted, np.asarray([[100.0]], dtype=np.float64)
        )[0]
        member_states = {
            member["algorithm"]: member["history_state"]
            for member in details["detectors"]
        }

        self.assertEqual(details["ensemble_state"], "insufficient_history")
        self.assertEqual(details["voted_count"], 1)
        self.assertEqual(details["eligible_count"], 1)
        self.assertEqual(details["configured_count"], 2)
        self.assertEqual(member_states["isolation_forest"], "insufficient_history")
        self.assertEqual(member_states["z_score"], "ready")

    def test_ensemble_configuration_validation(self) -> None:
        invalid_configurations = (
            {"members": [{"algorithm": "z_score"}]},
            {
                "members": [
                    {"algorithm": "z_score", "weight": 0},
                    {"algorithm": "isolation_forest"},
                ]
            },
            {
                "members": [
                    {"algorithm": "ewma", "features": ["x", "y"]},
                    {"algorithm": "z_score"},
                ]
            },
            {
                "members": [
                    {"algorithm": ["z_score"]},
                    {"algorithm": "isolation_forest"},
                ]
            },
            {
                "members": [
                    {"algorithm": "z_score"},
                    {"algorithm": "isolation_forest"},
                ],
                "voting_mode": [],
            },
        )
        for parameters in invalid_configurations:
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                EnsembleDetector.validate_config(parameters)

    def test_walk_forward_ensemble_persists_votes_and_is_reproducible(self) -> None:
        rng = np.random.default_rng(42)
        values = np.column_stack(
            [rng.normal(0, 0.2, 80), rng.normal(0, 0.2, 80)]
        )
        values[-4:] += 8
        configuration = EnsembleDetector.validate_config({
            "members": [
                {
                    "algorithm": "isolation_forest",
                    "parameters": {"n_estimators": 30, "random_state": 9},
                    "features": [0, 1],
                },
                {
                    "algorithm": "z_score",
                    "parameters": {"threshold": 3.0},
                    "features": [0, 1],
                },
            ],
            "voting_mode": "majority",
        })
        results = [
            walk_forward_predict(
                EnsembleDetector(),
                values,
                parameters=configuration,
                missing_values_strategy="median",
                scaling="standard",
            )
            for _ in range(2)
        ]
        np.testing.assert_array_equal(results[0].positions, results[1].positions)
        np.testing.assert_array_equal(results[0].predictions, results[1].predictions)
        np.testing.assert_allclose(results[0].scores, results[1].scores)
        self.assertEqual(results[0].result_details, results[1].result_details)
        self.assertTrue(
            all(
                "detectors" in details and "configuration" in details
                for details in results[0].result_details
            )
        )


class AnalysisComparisonApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Mock()
        self.user = SimpleNamespace(id=5, role="data_analyst")
        self.app = FastAPI()
        self.app.include_router(analyses_router, prefix="/analyses")
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()

    def _runs(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=11,
                status="completed",
                source_type="dataset",
                dataset_id=7,
                device_id=None,
                metrics={"configuration": {"features": ["sensor_1"]}, "execution_seconds": 1.2},
                total_records=50,
                anomalies_found=4,
                model_config=SimpleNamespace(name="IF", algorithm="isolation_forest"),
            ),
            SimpleNamespace(
                id=12,
                status="completed",
                source_type="dataset",
                dataset_id=7,
                device_id=None,
                metrics={"configuration": {"features": ["sensor_1"]}, "execution_seconds": 2.4},
                total_records=50,
                anomalies_found=6,
                model_config=SimpleNamespace(name="Ensemble", algorithm="ensemble"),
            ),
        ]

    def test_create_comparison_persists_metrics_matrix_and_audit_log(self) -> None:
        self.db.scalars.return_value = self._runs()
        self.db.scalar.side_effect = [3, 1, 3]
        self.db.execute.return_value.one.return_value = (
            50, 0.1, 0.9, 0.4, 0.2, 0.4, 0.6, 0.8,
        )
        self.db.execute.return_value.all.return_value = [("ready", 50)]
        added: list[object] = []

        def capture_add(record: object) -> None:
            added.append(record)
            if isinstance(record, AnalysisComparison):
                record.id = 31

        self.db.add.side_effect = capture_add
        response = self.client.post(
            "/analyses/comparisons", json={"analysis_ids": [11, 12]}
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["id"], 31)
        self.assertEqual(len(body["runs"]), 2)
        self.assertEqual(body["runs"][0]["score_distribution"]["count"], 50)
        self.assertEqual(body["runs"][0]["history_state_counts"], {"ready": 50})
        pair = next(
            row for row in body["agreement_matrix"]
            if row["analysis_id"] == 11 and row["other_analysis_id"] == 12
        )
        self.assertEqual(pair["both"], 3)
        self.assertTrue(any(isinstance(record, AnalysisComparison) for record in added))
        self.assertTrue(any(isinstance(record, AuditLog) for record in added))
        self.db.commit.assert_called_once()

    def test_comparison_rejects_runs_with_incompatible_sources(self) -> None:
        runs = self._runs()
        runs[1].dataset_id = 8
        self.db.scalars.return_value = runs
        response = self.client.post(
            "/analyses/comparisons", json={"analysis_ids": [11, 12]}
        )
        self.assertEqual(response.status_code, 422)

    def test_comparison_creation_requires_analyst_role(self) -> None:
        self.user.role = "maintenance_technician"
        response = self.client.post(
            "/analyses/comparisons", json={"analysis_ids": [11, 12]}
        )
        self.assertEqual(response.status_code, 403)

    def test_persisted_comparison_can_be_exported_as_csv(self) -> None:
        self.db.get.return_value = SimpleNamespace(
            id=31,
            analysis_ids=[11, 12],
            snapshot={
                "runs": [{
                    "analysis_id": 11,
                    "algorithm": "isolation_forest",
                    "status": "completed",
                    "total_records": 50,
                    "anomalies_found": 4,
                    "anomaly_rate": 0.08,
                    "score_distribution": {
                        "count": 50, "min": 0.1, "max": 0.9, "mean": 0.4,
                        "p25": 0.2, "median": 0.4, "p75": 0.6, "p95": 0.8,
                    },
                    "runtime_seconds": 1.2,
                    "history_state_counts": {"ready": 50},
                }],
                "agreement_matrix": [],
            },
            created_at=datetime.now(timezone.utc),
        )
        response = self.client.get("/analyses/comparisons/31/csv")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.text.startswith("record_type,analysis_id"))
        self.assertIn("isolation_forest", response.text)

if __name__ == "__main__":
    unittest.main()
