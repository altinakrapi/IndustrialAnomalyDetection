import unittest

from app.modules.analyses.application.evaluation_metrics import (
    aggregate_metric_runs,
    binary_metrics,
    label_readings,
)


class EvaluationMetricTests(unittest.TestCase):
    def test_confusion_precision_recall_f1_and_false_alarm_rate(self) -> None:
        actual = [True, True, False, False]
        predicted = [True, False, True, False]
        result = binary_metrics(actual, predicted, [0.9, 0.6, 0.8, 0.1])

        self.assertEqual(result["confusion_matrix"], {
            "true_positive": 1,
            "false_positive": 1,
            "false_negative": 1,
            "true_negative": 1,
        })
        self.assertEqual(result["precision"], 0.5)
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(result["f1"], 0.5)
        self.assertEqual(result["false_alarm_rate"], 0.5)
        self.assertEqual(result["roc_auc"], 0.75)
        self.assertEqual(result["pr_auc"], 0.8333333333333333)

    def test_tolerance_is_next_readings_after_fault_interval(self) -> None:
        labels, types = label_readings(
            [0, 1, 2, 3, 4, 5],
            [{"type": "slow_drift", "start_seconds": 2, "end_seconds": 3}],
            tolerance_readings=1,
        )

        self.assertEqual(labels, [False, False, True, True, True, False])
        self.assertEqual(types[4], ["slow_drift"])

    def test_rejects_negative_tolerance(self) -> None:
        with self.assertRaises(ValueError):
            label_readings([], [], -1)

    def test_empty_labels_do_not_produce_classification_metrics(self) -> None:
        result = binary_metrics([], [])

        self.assertIsNone(result["confusion_matrix"])
        self.assertIsNone(result["precision"])
        self.assertIsNone(result["recall"])
        self.assertIsNone(result["f1"])
        self.assertIsNone(result["false_alarm_rate"])
        self.assertIsNone(result["roc_auc"])
        self.assertIsNone(result["pr_auc"])

    def test_multirun_aggregation_reports_mean_and_population_spread(self) -> None:
        result = aggregate_metric_runs([
            {"f1": 0.0, "precision": 0.2},
            {"f1": 1.0, "precision": 0.8},
        ])

        self.assertEqual(result["f1"]["mean"], 0.5)
        self.assertEqual(result["f1"]["spread_stddev"], 0.5)
        self.assertEqual(result["precision"]["mean"], 0.5)
        self.assertEqual(result["roc_auc"]["run_count"], 0)


if __name__ == "__main__":
    unittest.main()
