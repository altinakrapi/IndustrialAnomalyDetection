import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.analyses.application.simulator_tuning import (
    _binary_metrics,
    _scores,
    verify_stored_f1,
)
from app.modules.analyses.api.simulator_tuning import run_7_save_decision, tuning_payload
from app.shared.database.base import Base
import numpy as np
from app.modules.analyses.domain.detectors import DETECTOR_REGISTRY, walk_forward_predict


class SimulatorTuningTests(unittest.TestCase):
    def test_tuning_payload_labels_superseded_runs_from_stored_confusion_counts(self) -> None:
        run = SimpleNamespace(
            id=1,
            created_by=1,
            status="completed",
            objective="maximize_f1_with_false_alarm_cap",
            false_alarm_cap=0.05,
            ground_truth_run_ids=[101],
            data_seeds=[101],
            configuration={},
            candidates={},
            result={"chosen": {
                "true_positive": 76,
                "false_positive": 5,
                "false_negative": 64,
                "true_negative": 331,
            }},
            runtime_seconds=1.0,
            limitations="Development only",
            created_at=None,
        )

        payload = tuning_payload(run)

        self.assertTrue(payload["presentation"]["superseded_by_run_7"])
        self.assertEqual(payload["presentation"]["scored_positions"], 476)

    def test_run_7_only_allows_reliable_three_detector_configs(self) -> None:
        row = {
            "algorithm": "ewma",
            "runs": [
                {"tuned_f1": 0.8, "default_f1": 0.7, "tuned_false_alarm_rate": 0.02},
                {"tuned_f1": 0.79, "default_f1": 0.75, "tuned_false_alarm_rate": 0.03},
            ],
        }
        should_save, _ = run_7_save_decision(row)
        self.assertTrue(should_save)

        row["runs"][1]["tuned_f1"] = row["runs"][1]["default_f1"]
        should_save, _ = run_7_save_decision(row)
        self.assertFalse(should_save)
    def test_two_member_majority_requires_both_votes(self) -> None:
        isolation_forest = np.asarray([1, 1, 0, 0])
        z_score = np.asarray([1, 0, 1, 0])
        self.assertEqual(
            (isolation_forest + z_score >= 2).tolist(),
            [True, False, False, False],
        )
    def test_binary_metrics_and_false_alarm_rate(self) -> None:
        metrics = _binary_metrics(
            np.asarray([0, 0, 1, 1]),
            np.asarray([0, 1, 1, 0]),
        )
        self.assertEqual(metrics["true_positive"], 1)
        self.assertEqual(metrics["false_positive"], 1)
        self.assertEqual(metrics["false_negative"], 1)
        self.assertEqual(metrics["false_alarm_rate"], 0.5)

    def test_time_ordered_scores_are_deterministic(self) -> None:
        values = np.asarray([1.0, 1.1, 0.9, 4.0, 4.1, 4.0, 1.0, 1.0])
        first = _scores(values, {
            "algorithm": "isolation_forest",
            "n_estimators": 100,
            "contamination": 0.05,
            "mode": "batch",
        })
        second = _scores(values, {
            "algorithm": "isolation_forest",
            "n_estimators": 100,
            "contamination": 0.05,
            "mode": "batch",
        })
        np.testing.assert_array_equal(first, second)

    def test_extended_detector_scores_are_deterministic(self) -> None:
        values = np.asarray([1.0, 1.1, 0.9, 4.0, 4.1, 4.0, 1.0, 1.0])
        for algorithm, extra in (
            ("one_class_svm", {"nu": 0.05, "gamma": "scale"}),
            ("local_outlier_factor", {"neighbors": 3, "contamination": 0.05}),
            ("autoencoder", {"hidden_sizes": [8, 4], "alpha": 0.001, "threshold_percentile": 95}),
        ):
            parameters = {"algorithm": algorithm, **extra}
            first = _scores(values, parameters)
            second = _scores(values, parameters)
            np.testing.assert_array_equal(first, second)

    def test_control_chart_tuning_scoring_positions_match_production(self) -> None:
        values = np.arange(80, dtype=float).reshape(-1, 1)
        detector = DETECTOR_REGISTRY["ewma"]()
        result = walk_forward_predict(
            detector,
            values,
            parameters={"lambda": 0.2, "L": 3.0, "baseline_window": 30},
            missing_values_strategy="mean",
            scaling="standard",
        )
        self.assertEqual(int(result.positions[0]), 30)
        self.assertEqual(len(result.positions), 50)

    def test_production_training_window_is_used_by_tuning(self) -> None:
        values = np.arange(80, dtype=float).reshape(-1, 1)
        detector = DETECTOR_REGISTRY["ewma"]()
        result = walk_forward_predict(
            detector,
            values,
            parameters={
                "lambda": 0.2,
                "L": 3.0,
                "baseline_window": 30,
                "training_window": 100,
                "refit_every": 10,
            },
            missing_values_strategy="mean",
            scaling="standard",
            batch_size=10,
        )
        self.assertEqual(int(result.positions[0]), 30)

    def test_f1_is_consistent_with_confusion_counts(self) -> None:
        metrics = {
            "true_positive": 42,
            "false_positive": 23,
            "false_negative": 532,
            "precision": 0.6461538461538462,
            "recall": 0.07317073170731707,
            "f1": 0.13145539906103287,
        }
        self.assertAlmostEqual(
            metrics["f1"],
            verify_stored_f1(metrics),
            places=12,
        )


if __name__ == "__main__":
    unittest.main()
