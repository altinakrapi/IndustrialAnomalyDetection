import unittest

from app.modules.devices.application.severity import MINIMUM_SCORE_HISTORY, percentile_severity


class PercentileSeverityTests(unittest.TestCase):
    def test_sufficient_history_uses_95th_and_995th_percentiles(self) -> None:
        scores = [float(index) for index in range(20)]

        medium, _ = percentile_severity(10.0, scores)
        high, high_reason = percentile_severity(18.2, scores)
        critical, critical_reason = percentile_severity(21.0, scores)

        self.assertEqual(medium, "medium")
        self.assertEqual(high, "high")
        self.assertEqual(critical, "critical")
        self.assertIn("20 earlier scores", high_reason)
        self.assertIn("earlier detector scores", critical_reason)

    def test_insufficient_history_returns_medium_with_reason(self) -> None:
        severity, reason = percentile_severity(100.0, range(MINIMUM_SCORE_HISTORY - 1))

        self.assertEqual(severity, "medium")
        self.assertEqual(reason, "insufficient history for severity")

    def test_input_history_does_not_include_current_score(self) -> None:
        severity, reason = percentile_severity(
            1000.0,
            [10.0, 11.0] * 10,
        )

        self.assertEqual(severity, "critical")
        self.assertIn("20 earlier scores", reason)
