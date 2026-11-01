import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.modules.anomaly_detection.api.router import (
    _explanation_payload,
    _feedback_statistics_payload,
)


def anomaly(alert_rules, expected_range=None, details=None):
    analysis = SimpleNamespace(
        metrics={"configuration": {"name": "stored-config", "version": 2, "parameters": {"threshold": 3}}},
        model_config=SimpleNamespace(name="Z-score", parameters={"version": 2}, id=1),
        source_type="sensor",
    )
    if details is not None:
        expected_range = {**(expected_range or {}), "detector_details": details}
    return SimpleNamespace(
        alert_rules=alert_rules,
        alert_priority="high",
        alert_episode_id=7,
        feature_values={"vibration": 12.0},
        expected_range=expected_range or {},
        history_size=80,
        history_state="ready",
        history_required=30,
        equipment_measurement=SimpleNamespace(source_type="sensor"),
        analysis_run=analysis,
        anomaly_type="z_score",
    )


class ExplanationPayloadTests(unittest.TestCase):
    def test_every_alert_rule_is_reported_from_stored_rules(self):
        for rule in ("physical_limit", "model", "flatline", "persistence", "stopped_reporting"):
            with self.subTest(rule=rule):
                payload = _explanation_payload(anomaly([rule]))
                self.assertIn(payload["rules_fired"][0], ("physical limit", "model", "flatline", "persistence", "stopped reporting"))
        self.assertEqual(
            _explanation_payload(anomaly(["physical_limit"]))["evidence_note"],
            "statistical evidence for human review",
        )

    def test_normalized_deviation_and_model_metadata_use_stored_values(self):
        payload = _explanation_payload(anomaly(
            ["model"],
            expected_range={"top_feature_deviations": [{
                "feature": "vibration",
                "baseline_mean": 10.0,
                "baseline_standard_deviation": 0.5,
                "absolute_z_score": 4.0,
            }]},
        ))
        contribution = payload["contributing_parameters"][0]
        self.assertEqual(contribution["value"], 12.0)
        self.assertEqual(contribution["baseline_mean"], 10.0)
        self.assertEqual(contribution["baseline_standard_deviations"], 4.0)
        self.assertEqual(contribution["history_size"], 80)
        self.assertEqual(payload["model"]["version"], 2)

    def test_ensemble_members_and_voting_rule_are_stored_only(self):
        payload = _explanation_payload(anomaly(["model"], details={
            "confidence": 0.75,
            "voting": "weighted",
            "detectors": [{
                "algorithm": "z_score",
                "vote": True,
                "score": 3.5,
                "history_state": "ready",
                "execution_mode": "walk_forward",
            }],
        }))
        self.assertEqual(payload["ensemble"]["confidence"], 0.75)
        self.assertEqual(payload["ensemble"]["voting_rule"], "weighted")
        self.assertEqual(payload["ensemble"]["members"][0]["execution_mode"], "walk_forward")


class FeedbackStatisticsTests(unittest.TestCase):
    def test_minimum_review_count_hides_percentage(self):
        registered = SimpleNamespace(
            id=1,
            analysis_run=SimpleNamespace(device=SimpleNamespace(name="Pump 1")),
            anomaly_type="z_score",
        )
        feedback = SimpleNamespace(anomaly_id=1, label="false_alarm", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        payload = _feedback_statistics_payload([registered], [feedback], minimum_reviewed=30)
        self.assertFalse(payload["sufficient_cases"])
        self.assertIsNone(payload["false_alarm_percentage"])

    def test_dataset_feedback_is_excluded_by_eligible_input(self):
        payload = _feedback_statistics_payload([], [
            SimpleNamespace(anomaly_id=99, label="false_alarm", updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ], 30)
        self.assertEqual(payload["reviewed_anomalies"], 0)
        self.assertEqual(payload["excluded"]["source_types"], ["dataset"])


if __name__ == "__main__":
    unittest.main()
