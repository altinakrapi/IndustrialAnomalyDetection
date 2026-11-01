import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from app.modules.monitoring.api.router import _schedule_payload
from app.workers.monitoring_scheduler import (
    _completed_non_simulated_run,
    _scheduled_execution_key,
)


class MonitoringScheduleTests(unittest.TestCase):
    def setUp(self):
        self.dataset = SimpleNamespace(
            id=4,
            row_count=120,
            uploaded_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            file_path="uploads/ims.csv",
            column_metadata=[{"name": "rms", "type": "numeric"}],
        )
        self.config = SimpleNamespace(id=9)

    def test_execution_key_is_stable_for_a_snapshot_and_changes_with_it(self):
        key = _scheduled_execution_key(self.dataset, self.config)
        self.assertEqual(key, _scheduled_execution_key(self.dataset, self.config))

        self.dataset.row_count += 1
        self.assertNotEqual(key, _scheduled_execution_key(self.dataset, self.config))

    def test_completed_legacy_analysis_is_reused_as_current_snapshot(self):
        legacy = SimpleNamespace(execution_key="initial:4:9")
        db = Mock()
        db.scalars.return_value = []
        db.scalar.side_effect = [None, legacy]
        current_key = _scheduled_execution_key(self.dataset, self.config)

        self.assertTrue(
            _completed_non_simulated_run(db, self.dataset, self.config, current_key)
        )
        self.assertEqual(legacy.execution_key, current_key)

    def test_active_run_prevents_overlapping_schedule_work(self):
        db = Mock()
        active_run = SimpleNamespace(
            started_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            status="running",
            error_message=None,
            completed_at=None,
        )
        db.scalars.return_value = [active_run]

        self.assertTrue(
            _completed_non_simulated_run(
                db,
                self.dataset,
                self.config,
                _scheduled_execution_key(self.dataset, self.config),
            )
        )
        db.scalars.assert_called_once()

    def test_stale_active_run_is_failed_without_blocking_scheduled_work(self):
        db = Mock()
        stale = SimpleNamespace(
            started_at=None,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            status="running",
            error_message=None,
            completed_at=None,
        )
        db.scalars.return_value = [stale]
        db.scalar.side_effect = [None, None, None]

        self.assertFalse(
            _completed_non_simulated_run(
                db,
                self.dataset,
                self.config,
                _scheduled_execution_key(self.dataset, self.config),
            )
        )
        self.assertEqual(stale.status, "failed")
        self.assertIn("stopped before completion", stale.error_message)
        self.assertIsNotNone(stale.completed_at)

    def test_schedule_status_reflects_running_failure_and_disabled_states(self):
        now = datetime.now(timezone.utc)
        schedule = SimpleNamespace(
            enabled=True,
            interval_minutes=360,
            simulation_enabled=False,
            simulation_batch_size=100,
            next_run_at=now,
            last_started_at=now,
            last_completed_at=None,
            last_error=None,
        )
        self.assertEqual(_schedule_payload(schedule)["execution_status"], "running")

        schedule.last_completed_at = now
        schedule.last_error = "analysis failed"
        self.assertEqual(_schedule_payload(schedule)["execution_status"], "failed")

        schedule.last_error = None
        schedule.enabled = False
        self.assertEqual(_schedule_payload(schedule)["execution_status"], "completed")

        schedule.last_completed_at = None
        schedule.last_started_at = None
        self.assertEqual(_schedule_payload(schedule)["execution_status"], "disabled")


if __name__ == "__main__":
    unittest.main()
