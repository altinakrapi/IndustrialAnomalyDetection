import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from fastapi import HTTPException

from app.modules.devices.api.equipment import (
    EquipmentMeasurementInput,
    record_equipment_measurement,
)
from app.modules.devices.application.alerting import (
    evaluate_alert_state,
    evaluate_flatline_values,
)
from app.modules.devices.application.analysis import analyze_equipment_measurement
from app.modules.devices.application.analysis import _robust_z_score
from app.shared.models import AlertEpisode, AnalysisResult, AnalysisRun, Anomaly
from app.workers.alert_escalation import escalate_unreviewed_alerts


def _parameter(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": 8,
        "device_id": 3,
        "consecutive_anomaly_threshold": None,
        "persistence_window_size": None,
        "persistence_anomaly_threshold": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _measurement(
    parameter: SimpleNamespace,
    measurement_id: int,
    *,
    value: float = 1.0,
    measured_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=measurement_id,
        parameter=parameter,
        value=value,
        unit="mm/s",
        measured_at=measured_at or datetime(2026, 10, 27, tzinfo=timezone.utc),
        recorded_by=7,
        source="manual",
        source_type="manual",
    )


def _database(
    previous: list[tuple[bool, int]] | None = None,
    episode: AlertEpisode | None = None,
) -> Mock:
    db = Mock()
    db.get.return_value = None
    db.execute.return_value = previous or []
    db.scalar.side_effect = [None, episode]

    def assign_id(record: object) -> None:
        if isinstance(record, AlertEpisode):
            record.id = 31

    db.add.side_effect = assign_id
    return db


class AlertPersistenceTests(unittest.TestCase):
    def test_flatline_rule_requires_baseline_and_detects_constant_window(self) -> None:
        insufficient = evaluate_flatline_values(
            [1.0, 1.1],
            [1.0] * 10,
            required_readings=10,
            variance_tolerance=0.05,
        )
        self.assertEqual(insufficient["history_state"], "insufficient_history")
        self.assertEqual(insufficient["history_size"], 2)
        self.assertEqual(insufficient["history_required"], 30)
        self.assertFalse(insufficient["triggered"])

        baseline = [1.0 + (index % 5) * 0.1 for index in range(30)]
        triggered = evaluate_flatline_values(
            baseline,
            [1.2] * 10,
            required_readings=10,
            variance_tolerance=0.05,
        )
        self.assertEqual(triggered["history_state"], "ready")
        self.assertTrue(triggered["triggered"])
        self.assertEqual(triggered["consecutive_readings"], 10)

    def test_flatline_rule_does_not_flag_variable_or_nonvariable_baselines(self) -> None:
        baseline = [1.0 + (index % 5) * 0.1 for index in range(30)]
        variable_window = evaluate_flatline_values(
            baseline,
            [1.0 + (index % 4) * 0.2 for index in range(10)],
            required_readings=10,
            variance_tolerance=0.05,
        )
        constant_baseline = evaluate_flatline_values(
            [2.0] * 30,
            [2.0] * 10,
            required_readings=10,
            variance_tolerance=0.05,
        )
        self.assertFalse(variable_window["triggered"])
        self.assertEqual(
            constant_baseline["history_state"], "baseline_not_variable"
        )
        self.assertFalse(constant_baseline["triggered"])

    def test_single_anomaly_is_watch_and_subsequent_readings_share_episode(self) -> None:
        parameter = _parameter()
        db = _database()
        first = evaluate_alert_state(
            db,
            _measurement(parameter, 1),
            history_size=20,
            history_required=20,
            is_anomalous=True,
            alert_rules=["model"],
            severity="medium",
        )

        self.assertEqual(first.priority, "watch")
        self.assertTrue(first.episode_created)
        self.assertEqual(first.episode.measurement_ids, [1])

        episode = first.episode
        self.assertIsNotNone(episode)
        for measurement_id in range(2, 6):
            db.scalar.side_effect = [None, episode]
            db.execute.return_value = [(True, measurement_id - 1)]
            current = evaluate_alert_state(
                db,
                _measurement(parameter, measurement_id),
                history_size=20,
                history_required=20,
                is_anomalous=True,
                alert_rules=["model"],
                severity="medium",
            )
            self.assertFalse(current.episode_created)
            self.assertEqual(current.episode.id, episode.id)

        self.assertEqual(episode.measurement_ids, [1, 2, 3, 4, 5])

    def test_three_consecutive_anomalies_raise_priority(self) -> None:
        state = evaluate_alert_state(
            _database([(True, 6), (True, 5)]),
            _measurement(_parameter(), 7),
            history_size=20,
            history_required=20,
            is_anomalous=True,
            alert_rules=["model"],
            severity="high",
        )

        self.assertEqual(state.consecutive_anomalies, 3)
        self.assertEqual(state.priority, "elevated")
        self.assertIn("persistence", state.alert_rules)

    def test_m_of_n_persistence_raises_priority_without_n_consecutive(self) -> None:
        parameter = _parameter(
            consecutive_anomaly_threshold=4,
            persistence_window_size=5,
            persistence_anomaly_threshold=3,
        )
        state = evaluate_alert_state(
            _database([(True, 6), (False, 5), (True, 4), (False, 3)]),
            _measurement(parameter, 7),
            history_size=20,
            history_required=20,
            is_anomalous=True,
            alert_rules=["model"],
            severity="high",
        )

        self.assertEqual(state.consecutive_anomalies, 2)
        self.assertEqual(state.window_anomalies, 3)
        self.assertEqual(state.priority, "elevated")
        self.assertIn("persistence", state.alert_rules)

    def test_insufficient_history_is_explicit_without_model_alert(self) -> None:
        state = evaluate_alert_state(
            _database(),
            _measurement(_parameter(), 1),
            history_size=2,
            history_required=20,
            is_anomalous=False,
            alert_rules=[],
            severity=None,
        )

        self.assertEqual(state.history_state, "insufficient_history")
        self.assertEqual(state.history_size, 2)
        self.assertEqual(state.history_required, 20)
        self.assertIsNone(state.episode)

    def test_episode_closes_after_configured_normal_readings(self) -> None:
        episode = AlertEpisode(
            id=31,
            device_id=3,
            parameter_id=8,
            status="open",
            priority="watch",
            severity="medium",
            measurement_ids=[1],
            alert_rules=["model"],
            history_size=20,
            history_required=20,
            consecutive_anomalies=1,
            window_anomalies=1,
            normal_readings=2,
            first_detected_at=datetime(2026, 10, 26, tzinfo=timezone.utc),
            last_detected_at=datetime(2026, 10, 26, tzinfo=timezone.utc),
        )
        state = evaluate_alert_state(
            _database(episode=episode),
            _measurement(
                _parameter(),
                2,
                measured_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
            ),
            history_size=20,
            history_required=20,
            is_anomalous=False,
            alert_rules=[],
            severity=None,
        )

        self.assertEqual(episode.status, "closed")
        self.assertEqual(episode.normal_readings, 3)
        self.assertIsNotNone(episode.closed_at)
        self.assertEqual(state.episode.id, episode.id)


class LayeredAnalysisTests(unittest.TestCase):
    @patch("app.modules.devices.application.analysis.notify_user")
    @patch("app.modules.devices.application.analysis.notify_roles")
    def test_flatline_rule_persists_alert_with_review_wording(
        self, _notify_roles: Mock, _notify_user: Mock
    ) -> None:
        measured_at = datetime(2026, 10, 27, tzinfo=timezone.utc)
        device = SimpleNamespace(
            id=3,
            name="Test bearing",
            device_code="TEST-3",
            is_registered=True,
            responsible_user_id=7,
        )
        parameter = SimpleNamespace(
            id=8,
            device_id=3,
            name="Vibration RMS",
            unit="mm/s",
            analysis_method="threshold",
            lower_limit=None,
            upper_limit=None,
            warning_low=None,
            warning_high=None,
            critical_low=None,
            critical_high=None,
            z_score_threshold=3.0,
            isolation_contamination=0.05,
            expected_interval_minutes=120,
            expected_interval_seconds=None,
            flatline_reading_count=10,
            flatline_variance_tolerance=0.05,
            consecutive_anomaly_threshold=None,
            persistence_window_size=None,
            persistence_anomaly_threshold=None,
            updated_at=measured_at,
            device=device,
        )
        measurement = _measurement(
            parameter, 40, value=1.2, measured_at=measured_at
        )
        measurement.source_type = "simulated"
        db = _database()
        db.scalars.return_value = list(
            reversed(
                [
                    *(1.0 + (index % 5) * 0.1 for index in range(30)),
                    *([1.2] * 9),
                ]
            )
        )

        def assign_id(record: object) -> None:
            if isinstance(record, AnalysisRun):
                record.id = 41
            elif isinstance(record, AlertEpisode):
                record.id = 31
            elif isinstance(record, Anomaly):
                record.id = 52

        db.add.side_effect = assign_id
        run = analyze_equipment_measurement(db, measurement)
        self.assertEqual(run.anomalies_found, 1, run.metrics)
        anomaly = next(
            call.args[0]
            for call in db.add.call_args_list
            if isinstance(call.args[0], Anomaly)
        )

        self.assertEqual(anomaly.anomaly_type, "flatline")
        self.assertEqual(anomaly.alert_rules, ["flatline"])
        self.assertIn(
            "sensor reading is constant, possible stuck sensor",
            anomaly.description,
        )
        self.assertEqual(
            anomaly.expected_range["flatline_evaluation"]["history_state"],
            "ready",
        )
        _notify_roles.assert_called_once()
        self.assertEqual(
            _notify_roles.call_args.kwargs["dedupe_key"], "alert-episode:31"
        )

    @patch("app.modules.devices.application.analysis.notify_user")
    @patch("app.modules.devices.application.analysis.notify_roles")
    def test_critical_limit_alerts_immediately_with_zero_history(
        self, _notify_roles: Mock, _notify_user: Mock
    ) -> None:
        measured_at = datetime(2026, 10, 27, tzinfo=timezone.utc)
        device = SimpleNamespace(
            id=3,
            name="Test bearing",
            device_code="TEST-3",
            is_registered=True,
            responsible_user_id=7,
        )
        parameter = SimpleNamespace(
            id=8,
            device_id=3,
            name="Vibration RMS",
            unit="mm/s",
            analysis_method="z_score",
            lower_limit=0.0,
            upper_limit=15.0,
            warning_low=None,
            warning_high=8.0,
            critical_low=None,
            critical_high=10.0,
            z_score_threshold=3.0,
            isolation_contamination=0.05,
            expected_interval_minutes=120,
            consecutive_anomaly_threshold=None,
            persistence_window_size=None,
            persistence_anomaly_threshold=None,
            updated_at=measured_at,
            device=device,
        )
        measurement = _measurement(
            parameter, 22, value=12.0, measured_at=measured_at
        )
        db = _database()
        db.scalars.return_value = []

        def assign_id(record: object) -> None:
            if isinstance(record, AnalysisRun):
                record.id = 41
            elif isinstance(record, AlertEpisode):
                record.id = 31
            elif isinstance(record, Anomaly):
                record.id = 52

        db.add.side_effect = assign_id

        run = analyze_equipment_measurement(db, measurement)

        self.assertEqual(run.anomalies_found, 1)
        self.assertEqual(run.metrics["limit_result"]["severity"], "critical")
        self.assertEqual(run.metrics["model_result"]["evaluated"], False)
        self.assertEqual(
            run.metrics["model_result"]["skipped_reason"],
            "physical_limit_alert",
        )
        self.assertEqual(
            run.metrics["alert_evaluation"]["history_state"],
            "insufficient_history",
        )
        self.assertEqual(run.metrics["alert_evaluation"]["history_size"], 0)
        self.assertEqual(
            run.metrics["alert_evaluation"]["history_required"], 8
        )
        anomaly = next(
            record
            for record in (call.args[0] for call in db.add.call_args_list)
            if isinstance(record, Anomaly)
        )
        self.assertEqual(anomaly.severity, "critical")
        self.assertEqual(anomaly.alert_rules, ["limit"])
        self.assertEqual(anomaly.alert_priority, "watch")
        self.assertTrue(
            any(
                isinstance(call.args[0], AnalysisResult)
                and call.args[0].is_anomaly
                for call in db.add.call_args_list
            )
        )
        _notify_roles.assert_called_once()
        _notify_user.assert_called_once()

    @patch("app.modules.devices.application.analysis.notify_user")
    @patch("app.modules.devices.application.analysis.notify_roles")
    @patch("app.modules.devices.application.analysis.evaluate_alert_state")
    def test_extreme_sensor_reading_is_statistically_flagged(
        self,
        evaluate: Mock,
        _notify_roles: Mock,
        _notify_user: Mock,
    ) -> None:
        measured_at = datetime(2026, 10, 27, tzinfo=timezone.utc)
        parameter = SimpleNamespace(
            id=8,
            device_id=3,
            name="Vibration RMS",
            unit="mm/s",
            analysis_method="z_score",
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
                id=3,
                name="Sensor bearing",
                device_code="SENSOR-3",
                is_registered=True,
                responsible_user_id=7,
            ),
        )
        measurement = _measurement(
            parameter, 22, value=1_000_000.0, measured_at=measured_at
        )
        measurement.source_type = "sensor"
        baseline = [0.8, 0.9, 1.0, 1.1, 0.9, 1.0, 1.1, 0.8]
        db = _database()
        db.scalars.return_value = baseline
        state = SimpleNamespace(
            history_state="ready",
            history_size=8,
            history_required=8,
            consecutive_anomalies=1,
            window_anomalies=1,
            priority="watch",
            alert_rules=["model"],
            episode=SimpleNamespace(id=31, status="open"),
            episode_created=True,
        )
        evaluate.return_value = state
        db.execute.return_value = Mock()
        db.execute.return_value.all.return_value = []
        db.add.side_effect = lambda record: (
            setattr(record, "id", 41)
            if isinstance(record, AnalysisRun)
            else setattr(record, "id", 52)
            if isinstance(record, Anomaly)
            else None
        )

        run = analyze_equipment_measurement(db, measurement)

        self.assertEqual(measurement.source_type, "sensor")
        self.assertEqual(measurement.value, 1_000_000.0)
        self.assertEqual(run.anomalies_found, 1)
        self.assertTrue(run.metrics["model_result"]["is_anomaly"])
        self.assertEqual(run.metrics["model_result"]["algorithm"], "z_score")
        self.assertGreater(
            run.metrics["model_result"]["score"],
            run.metrics["model_result"]["score_threshold"],
        )


class AlertNotificationTests(unittest.TestCase):
    @patch("app.modules.devices.application.analysis.notify_user")
    @patch("app.modules.devices.application.analysis.notify_roles")
    @patch("app.modules.devices.application.analysis.evaluate_alert_state")
    def test_many_anomalous_readings_create_one_episode_notification(
        self,
        evaluate: Mock,
        notify_roles: Mock,
        notify_user: Mock,
    ) -> None:
        measured_at = datetime(2026, 10, 27, tzinfo=timezone.utc)
        device = SimpleNamespace(
            id=3,
            name="Test bearing",
            device_code="TEST-3",
            is_registered=True,
            responsible_user_id=7,
        )
        parameter = SimpleNamespace(
            id=8,
            device_id=3,
            name="Vibration RMS",
            unit="mm/s",
            analysis_method="threshold",
            lower_limit=0.0,
            upper_limit=10.0,
            warning_low=None,
            warning_high=None,
            critical_low=None,
            critical_high=None,
            z_score_threshold=3.0,
            isolation_contamination=0.05,
            expected_interval_minutes=120,
            updated_at=measured_at,
            device=device,
        )
        episode = SimpleNamespace(id=31, status="open")
        evaluate.side_effect = [
            SimpleNamespace(
                history_state="ready",
                history_size=0,
                history_required=0,
                consecutive_anomalies=1,
                window_anomalies=1,
                priority="watch",
                alert_rules=["limit"],
                episode=episode,
                episode_created=True,
            ),
            SimpleNamespace(
                history_state="ready",
                history_size=1,
                history_required=0,
                consecutive_anomalies=2,
                window_anomalies=2,
                priority="watch",
                alert_rules=["limit"],
                episode=episode,
                episode_created=False,
            ),
        ]
        db = Mock()
        db.scalars.return_value = []
        next_run_id = 40
        next_anomaly_id = 50

        def assign_id(record: object) -> None:
            nonlocal next_run_id, next_anomaly_id
            if isinstance(record, AnalysisRun):
                next_run_id += 1
                record.id = next_run_id
            elif isinstance(record, Anomaly):
                next_anomaly_id += 1
                record.id = next_anomaly_id

        db.add.side_effect = assign_id
        for measurement_id in (22, 23):
            analyze_equipment_measurement(
                db,
                _measurement(
                    parameter,
                    measurement_id,
                    value=12.0,
                    measured_at=measured_at + timedelta(minutes=measurement_id),
                ),
            )

        self.assertEqual(evaluate.call_count, 2)
        notify_roles.assert_called_once()
        notify_user.assert_called_once()


class ManualMeasurementValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.device = SimpleNamespace(
            id=3,
            status="active",
            responsible_user_id=7,
        )
        self.parameter = SimpleNamespace(
            id=8,
            device_id=3,
            name="Vibration RMS",
            unit="mm/s",
            enabled=True,
        )
        self.user = SimpleNamespace(id=7, role="maintenance_technician")
        self.db = Mock()
        self.db.scalar.return_value = self.parameter

    @patch("app.modules.devices.api.equipment.persist_equipment_measurement")
    @patch("app.modules.devices.api.equipment.manual_extreme_score")
    @patch("app.modules.devices.api.equipment._registered_device")
    def test_extreme_manual_reading_requires_confirmation(
        self,
        registered_device: Mock,
        extreme_score: Mock,
        persist_measurement: Mock,
    ) -> None:
        registered_device.return_value = self.device
        extreme_score.return_value = (12.0, 10.0)
        payload = EquipmentMeasurementInput(
            parameter_id=8,
            value=100.0,
            idempotency_key="same-reading",
        )

        with self.assertRaises(HTTPException) as error:
            record_equipment_measurement(3, payload, self.user, self.db)

        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(
            error.exception.detail["code"], "manual_confirmation_required"
        )
        persist_measurement.assert_not_called()

    @patch("app.modules.devices.api.equipment._measurement_payload")
    @patch("app.modules.devices.api.equipment.persist_equipment_measurement")
    @patch("app.modules.devices.api.equipment.manual_extreme_score")
    @patch("app.modules.devices.api.equipment._registered_device")
    def test_confirmed_extreme_reading_uses_same_idempotency_key(
        self,
        registered_device: Mock,
        extreme_score: Mock,
        persist_measurement: Mock,
        measurement_payload: Mock,
    ) -> None:
        registered_device.return_value = self.device
        extreme_score.return_value = (12.0, 10.0)
        measurement = SimpleNamespace(id=22)
        run = SimpleNamespace(
            id=41, anomalies_found=1, status="completed", metrics={}
        )
        persist_measurement.return_value = (measurement, run, False)
        measurement_payload.return_value = {"id": 22, "source_type": "manual"}
        payload = EquipmentMeasurementInput(
            parameter_id=8,
            value=100.0,
            idempotency_key="same-reading",
            confirm_extreme=True,
        )

        result = record_equipment_measurement(3, payload, self.user, self.db)

        self.assertTrue(result["plausibility_check"]["confirmed"])
        self.assertEqual(
            persist_measurement.call_args.kwargs["idempotency_key"],
            "same-reading",
        )
        self.assertEqual(persist_measurement.call_args.args[3], 100.0)

    @patch("app.modules.devices.api.equipment.manual_extreme_score")
    @patch("app.modules.devices.api.equipment._registered_device")
    def test_physically_impossible_manual_value_is_rejected(
        self, registered_device: Mock, extreme_score: Mock
    ) -> None:
        registered_device.return_value = self.device
        payload = EquipmentMeasurementInput(
            parameter_id=8,
            value=-1.0,
            idempotency_key="invalid-reading",
        )

        with self.assertRaises(HTTPException) as error:
            record_equipment_measurement(3, payload, self.user, self.db)

        self.assertEqual(error.exception.status_code, 422)
        extreme_score.assert_not_called()


class AlertEscalationTests(unittest.TestCase):
    @patch("app.workers.alert_escalation.notify_roles")
    def test_unreviewed_episode_escalates_after_timeout_once(
        self, notify_roles: Mock
    ) -> None:
        now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        episode = SimpleNamespace(
            id=31,
            device_id=3,
            parameter_id=8,
            status="open",
            priority="watch",
            severity="high",
            alert_rules=["limit"],
            history_size=0,
            history_required=20,
            first_detected_at=now - timedelta(minutes=61),
            last_measurement_id=22,
            reviewed_at=None,
            escalated_at=None,
        )
        db = Mock()
        db.get.return_value = None
        db.scalars.side_effect = [[episode], []]

        count = escalate_unreviewed_alerts(db, now)

        self.assertEqual(count, 1)
        self.assertEqual(episode.priority, "escalated")
        self.assertEqual(episode.escalated_at, now)
        notify_roles.assert_called_once()
        self.assertEqual(notify_roles.call_args.args[1], {"administrator"})
        self.assertEqual(
            notify_roles.call_args.kwargs["dedupe_key"],
            "alert-escalation:31",
        )
        self.assertEqual(escalate_unreviewed_alerts(db, now), 0)
        notify_roles.assert_called_once()

    @patch("app.workers.monitoring_scheduler.escalate_unreviewed_alerts")
    @patch("app.workers.monitoring_scheduler._schedule_due_cycle")
    def test_scheduler_cycle_calls_escalation_under_advisory_lock(
        self, schedule_cycle: Mock, escalate: Mock
    ) -> None:
        from app.workers import monitoring_scheduler

        connection = MagicMock()
        connection.scalar.return_value = True
        engine_context = MagicMock()
        engine_context.__enter__.return_value = connection
        db_context = MagicMock()
        db_context.__enter__.return_value = Mock()

        with (
            patch.object(monitoring_scheduler.engine, "connect", return_value=engine_context),
            patch.object(monitoring_scheduler.SessionLocal, "begin", return_value=db_context),
        ):
            monitoring_scheduler.run_due_cycle()

        self.assertIn("pg_try_advisory_lock", str(connection.scalar.call_args.args[0]))
        self.assertIn("pg_advisory_unlock", str(connection.execute.call_args.args[0]))
        schedule_cycle.assert_called_once()
        escalate.assert_called_once()

    @patch("app.workers.monitoring_scheduler.escalate_unreviewed_alerts")
    @patch("app.workers.monitoring_scheduler._notify_overdue_equipment_measurements")
    def test_scheduler_escalates_when_scheduled_analysis_is_paused(
        self, notify_reporting: Mock, escalate: Mock
    ) -> None:
        from app.workers import monitoring_scheduler

        connection = MagicMock()
        connection.scalar.return_value = True
        engine_context = MagicMock()
        engine_context.__enter__.return_value = connection
        paused_schedule_db = Mock()
        paused_schedule_db.scalar.return_value = SimpleNamespace(enabled=False)
        schedule_context = MagicMock()
        schedule_context.__enter__.return_value = paused_schedule_db
        escalation_db = Mock()
        escalation_context = MagicMock()
        escalation_context.__enter__.return_value = escalation_db

        with (
            patch.object(monitoring_scheduler.engine, "connect", return_value=engine_context),
            patch.object(
                monitoring_scheduler.SessionLocal,
                "begin",
                side_effect=[schedule_context, escalation_context],
            ),
        ):
            monitoring_scheduler.run_due_cycle()

        self.assertIn("pg_try_advisory_lock", str(connection.scalar.call_args.args[0]))
        self.assertIn("pg_advisory_unlock", str(connection.execute.call_args.args[0]))
        notify_reporting.assert_called_once()
        escalate.assert_called_once_with(escalation_db)
