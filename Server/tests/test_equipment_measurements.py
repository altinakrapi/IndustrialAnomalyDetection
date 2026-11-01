import unittest
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.api.dependencies import get_current_user, get_db
from app.modules.devices.api.equipment import (
    EquipmentMeasurementInput,
    EquipmentParameterInput,
    create_equipment_parameter,
    list_equipment_parameters,
    router as equipment_router,
)
from app.modules.devices.api.router import list_devices
from app.modules.devices.application.analysis import _outside, _robust_z_score
from app.modules.devices.application.schedule import measurement_schedule
from app.modules.devices.application.stale_data import evaluate_stale_reporting
from app.shared.models import AlertEpisode, Device
from app.workers.monitoring_scheduler import (
    _notify_overdue_equipment_measurements,
    _schedule_due_cycle,
)


class EquipmentMeasurementValidationTests(unittest.TestCase):
    def test_new_parameters_default_to_two_hour_isolation_forest_monitoring(self) -> None:
        parameter = EquipmentParameterInput(name="Vibration RMS", unit="mm/s")

        self.assertEqual(parameter.expected_interval_minutes, 120)
        self.assertIsNone(parameter.expected_interval_seconds)
        self.assertEqual(parameter.analysis_method, "isolation_forest")
        self.assertEqual(parameter.flatline_reading_count, 10)
        self.assertEqual(parameter.flatline_variance_tolerance, 0.05)

    def test_flatline_and_reporting_intervals_are_validated(self) -> None:
        with self.assertRaises(ValidationError):
            EquipmentParameterInput(
                name="Vibration RMS",
                unit="mm/s",
                expected_interval_seconds=0,
            )
        with self.assertRaises(ValidationError):
            EquipmentParameterInput(
                name="Vibration RMS",
                unit="mm/s",
                flatline_reading_count=1,
            )
        with self.assertRaises(ValidationError):
            EquipmentParameterInput(
                name="Vibration RMS",
                unit="mm/s",
                flatline_variance_tolerance=0,
            )

    def test_parameter_requires_ordered_thresholds(self) -> None:
        with self.assertRaises(ValidationError):
            EquipmentParameterInput(
                name="temperature",
                unit="C",
                warning_low=20,
                critical_low=25,
            )

    def test_parameter_accepts_configured_z_score(self) -> None:
        parameter = EquipmentParameterInput(
            name=" vibration RMS ",
            unit="mm/s",
            analysis_method="z_score",
            warning_high=8,
            critical_high=12,
        )

        self.assertEqual(parameter.name, "vibration RMS")
        self.assertEqual(parameter.analysis_method, "z_score")

    def test_parameter_accepts_registry_ensemble_method(self) -> None:
        parameter = EquipmentParameterInput(
            name="vibration RMS",
            unit="mm/s",
            analysis_method="ensemble",
        )

        self.assertEqual(parameter.analysis_method, "ensemble")

    def test_measurement_requires_finite_value_and_rejects_manual_timestamp(self) -> None:
        with self.assertRaises(ValidationError):
            EquipmentMeasurementInput(
                parameter_id=1,
                value=2.0,
                measured_at=datetime.now(timezone.utc),
            )
        with self.assertRaises(ValidationError):
            EquipmentMeasurementInput(
                parameter_id=1,
                value=float("nan"),
            )
        with self.assertRaises(ValidationError):
            EquipmentMeasurementInput(
                parameter_id=1,
                value=2.0,
                measured_at=datetime.now(timezone.utc),
            )

    def test_measurement_input_contains_values_but_no_timestamp(self) -> None:
        measurement = EquipmentMeasurementInput(
            parameter_id=1,
            value=2.0,
        )

        self.assertEqual(measurement.parameter_id, 1)
        self.assertFalse(hasattr(measurement, "measured_at"))

    def test_assigned_recorder_can_list_device_parameters(self) -> None:
        db = Mock()
        device = SimpleNamespace(
            id=2, is_registered=True, responsible_user_id=7
        )
        db.get.side_effect = lambda model, *_args: (
            device if model is Device else None
        )
        db.scalars.return_value = []

        parameters = list_equipment_parameters(
            device_id=2,
            user=SimpleNamespace(id=7, role="maintenance_technician"),
            db=db,
        )

        self.assertEqual(parameters, [])

    def test_device_parameter_route_returns_empty_list_when_unconfigured(self) -> None:
        db = Mock()
        db.get.return_value = SimpleNamespace(
            id=2, is_registered=True, responsible_user_id=7
        )
        db.scalars.return_value = []
        app = FastAPI()
        app.include_router(equipment_router, prefix="/devices")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=7, role="maintenance_technician"
        )
        app.dependency_overrides[get_db] = lambda: db

        with TestClient(app) as client:
            response = client.get("/devices/2/parameters")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])

    def test_unassigned_recorder_cannot_list_device_parameters(self) -> None:
        db = Mock()
        db.get.return_value = SimpleNamespace(
            id=2, is_registered=True, responsible_user_id=7
        )

        with self.assertRaises(HTTPException) as context:
            list_equipment_parameters(
                device_id=2,
                user=SimpleNamespace(id=8, role="maintenance_technician"),
                db=db,
            )

        self.assertEqual(getattr(context.exception, "status_code", None), 404)

    @patch(
        "app.modules.devices.api.equipment._parameter_payload",
        return_value={"id": 12, "name": "Vibration RMS"},
    )
    def test_assigned_recorder_can_configure_parameter_interval(
        self, parameter_payload: Mock
    ) -> None:
        db = Mock()
        device = SimpleNamespace(
            id=2, is_registered=True, responsible_user_id=7
        )
        db.get.side_effect = lambda model, *_args: (
            device if model is Device else None
        )

        result = create_equipment_parameter(
            device_id=2,
            payload=EquipmentParameterInput(
                name="Vibration RMS",
                unit="mm/s",
                expected_interval_minutes=90,
            ),
            user=SimpleNamespace(id=7, role="maintenance_technician"),
            db=db,
        )

        self.assertEqual(result["id"], 12)
        parameter = db.add.call_args_list[0].args[0]
        self.assertEqual(parameter.expected_interval_minutes, 90)
        self.assertEqual(parameter.device_id, 2)
        parameter_payload.assert_called_once()

    def test_unassigned_recorder_cannot_configure_parameter(self) -> None:
        db = Mock()
        db.get.return_value = SimpleNamespace(
            id=2, is_registered=True, responsible_user_id=7
        )

        with self.assertRaises(HTTPException) as context:
            create_equipment_parameter(
                device_id=2,
                payload=EquipmentParameterInput(
                    name="Vibration RMS",
                    unit="mm/s",
                ),
                user=SimpleNamespace(id=8, role="maintenance_technician"),
                db=db,
            )

        self.assertEqual(context.exception.status_code, 404)
        db.add.assert_not_called()

    def test_device_list_is_scoped_for_non_administrators(self) -> None:
        db = Mock()
        db.scalars.return_value = []

        list_devices(
            search=None,
            status_filter=None,
            category=None,
            location=None,
            sort_by="name",
            sort_order="asc",
            limit=100,
            offset=0,
            user=SimpleNamespace(id=8, role="data_analyst"),
            db=db,
        )

        self.assertIn("devices.responsible_user_id = :responsible_user_id_1", str(
            db.scalars.call_args.args[0]
        ))


class EquipmentAnalysisHelperTests(unittest.TestCase):
    def test_threshold_checks_respect_optional_bounds(self) -> None:
        self.assertTrue(_outside(15.0, None, 10.0))
        self.assertFalse(_outside(5.0, None, 10.0))

    def test_robust_z_score_uses_median_and_mad(self) -> None:
        score, center, scale = _robust_z_score(20.0, [1, 2, 3, 4, 5, 6, 7, 8])

        self.assertIsNotNone(score)
        self.assertEqual(center, 4.5)
        self.assertGreater(score, 3.0)

    def test_measurement_schedule_uses_configured_interval_and_no_fake_reading(self) -> None:
        created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        parameter = SimpleNamespace(
            created_at=created_at,
            expected_interval_minutes=120,
            expected_interval_seconds=None,
            enabled=True,
        )

        due_at, status = measurement_schedule(
            parameter, None, datetime(2026, 1, 1, 1, 59, tzinfo=timezone.utc)
        )

        self.assertEqual(due_at, datetime(2026, 1, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(status, "awaiting_first_measurement")

    def test_measurement_schedule_marks_due_parameters_overdue(self) -> None:
        created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        parameter = SimpleNamespace(
            created_at=created_at,
            expected_interval_minutes=30,
            expected_interval_seconds=None,
            enabled=True,
        )

        due_at, status = measurement_schedule(
            parameter, None, datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
        )

        self.assertEqual(due_at, datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc))
        self.assertEqual(status, "overdue")

    def test_measurement_schedule_supports_second_based_interval(self) -> None:
        created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        parameter = SimpleNamespace(
            created_at=created_at,
            expected_interval_minutes=120,
            expected_interval_seconds=3,
            enabled=True,
        )
        due_at, status = measurement_schedule(
            parameter,
            created_at,
            created_at + timedelta(seconds=2),
        )
        self.assertEqual(due_at, created_at + timedelta(seconds=3))
        self.assertEqual(status, "scheduled")

    @patch("app.workers.monitoring_scheduler.notify_user")
    @patch("app.workers.monitoring_scheduler.notify_roles")
    def test_overdue_measurement_notice_is_deduplicated_per_last_reading(
        self, notify_roles: Mock, notify_user: Mock
    ) -> None:
        last_reading = datetime(2026, 1, 1, tzinfo=timezone.utc)
        parameter = SimpleNamespace(
            id=3,
            device_id=9,
            name="temperature",
            expected_interval_minutes=60,
            expected_interval_seconds=None,
            enabled=True,
            created_at=last_reading,
            device=SimpleNamespace(
                name="Pump", responsible_user_id=None, source_type="manual"
            ),
        )
        db = Mock()
        db.scalars.return_value = [parameter]
        db.scalar.return_value = SimpleNamespace(
            measured_at=last_reading,
            received_at=last_reading,
            source_type="manual",
        )

        _notify_overdue_equipment_measurements(
            db, datetime(2026, 1, 1, 2, tzinfo=timezone.utc)
        )

        notify_roles.assert_called_once()
        self.assertEqual(
            notify_roles.call_args.kwargs["dedupe_key"],
            f"measurement-overdue:3:{(last_reading.replace(hour=1)).isoformat()}",
        )
        notify_user.assert_called_once()

    @patch("app.workers.monitoring_scheduler.notify_user")
    @patch("app.workers.monitoring_scheduler.notify_roles")
    def test_on_time_measurement_does_not_create_overdue_notice(
        self, notify_roles: Mock, notify_user: Mock
    ) -> None:
        last_reading = datetime(2026, 1, 1, tzinfo=timezone.utc)
        parameter = SimpleNamespace(
            id=3,
            device_id=9,
            name="temperature",
            expected_interval_minutes=60,
            expected_interval_seconds=None,
            enabled=True,
            created_at=last_reading,
            device=SimpleNamespace(
                name="Pump", responsible_user_id=None, source_type="manual"
            ),
        )
        db = Mock()
        db.scalars.return_value = [parameter]
        db.scalar.return_value = SimpleNamespace(
            measured_at=last_reading,
            received_at=last_reading,
            source_type="manual",
        )

        _notify_overdue_equipment_measurements(
            db, datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
        )

        notify_roles.assert_not_called()
        notify_user.assert_not_called()


class StaleReportingAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        self.parameter = SimpleNamespace(
            id=3,
            device_id=9,
            name="Vibration RMS",
            expected_interval_minutes=1,
            expected_interval_seconds=3,
            created_at=self.now - timedelta(minutes=1),
            device=SimpleNamespace(
                id=9,
                name="Simulator bearing",
                device_code="SIM-9",
                source_type="simulated",
                responsible_user_id=7,
            ),
        )
        self.latest = SimpleNamespace(
            id=44,
            source_type="simulated",
            received_at=self.now - timedelta(seconds=10),
        )

    @patch("app.modules.devices.application.stale_data.notify_user")
    @patch("app.modules.devices.application.stale_data.notify_roles")
    def test_stale_reporting_opens_once_and_closes_when_reading_resumes(
        self, notify_roles: Mock, notify_user: Mock
    ) -> None:
        db = Mock()
        db.get.return_value = None
        episode = None

        def find_episode(*_args, **_kwargs):
            return episode

        def add_record(record: object) -> None:
            nonlocal episode
            if isinstance(record, AlertEpisode):
                record.id = 31
                episode = record

        db.scalar.side_effect = find_episode
        db.add.side_effect = add_record

        created = evaluate_stale_reporting(
            db, self.parameter, self.latest, self.now
        )
        self.assertIsNotNone(created)
        self.assertEqual(created.alert_rules, ["stale_data"])
        self.assertEqual(created.status, "open")
        self.assertEqual(notify_roles.call_count, 1)
        self.assertEqual(
            notify_roles.call_args.kwargs["dedupe_key"],
            "stale-data:31:opened",
        )
        self.assertIn("[SIMULATED]", notify_roles.call_args.args[2])

        evaluate_stale_reporting(db, self.parameter, self.latest, self.now)
        self.assertEqual(notify_roles.call_count, 1)
        self.assertEqual(notify_user.call_count, 1)

        resumed_at = self.now + timedelta(seconds=2)
        resumed = SimpleNamespace(
            id=45,
            source_type="simulated",
            received_at=resumed_at,
        )
        db.scalar.side_effect = lambda *_args, **_kwargs: episode
        closed = evaluate_stale_reporting(
            db, self.parameter, resumed, resumed_at
        )
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.closed_at, resumed_at)
        self.assertEqual(notify_roles.call_count, 2)
        self.assertEqual(
            notify_roles.call_args.kwargs["dedupe_key"],
            "stale-data:31:resolved",
        )

    @patch("app.modules.devices.application.stale_data.notify_user")
    @patch("app.modules.devices.application.stale_data.notify_roles")
    def test_manual_reporting_is_not_classified_as_stale_telemetry(
        self, notify_roles: Mock, notify_user: Mock
    ) -> None:
        self.parameter.device.source_type = "manual"
        manual_measurement = SimpleNamespace(
            id=45,
            source_type="manual",
            received_at=self.now - timedelta(days=1),
        )
        db = Mock()

        result = evaluate_stale_reporting(
            db, self.parameter, manual_measurement, self.now
        )

        self.assertIsNone(result)
        db.scalar.assert_not_called()
        notify_roles.assert_not_called()
        notify_user.assert_not_called()

    @patch("app.workers.monitoring_scheduler._notify_overdue_equipment_measurements")
    @patch("app.workers.monitoring_scheduler.SessionLocal")
    def test_stale_reporting_sweep_runs_when_scheduled_analysis_is_paused(
        self, session_local: Mock, notify_reporting: Mock
    ) -> None:
        db = Mock()
        db.scalar.return_value = SimpleNamespace(enabled=False)
        session_local.begin.return_value.__enter__.return_value = db

        _schedule_due_cycle()

        notify_reporting.assert_called_once()


if __name__ == "__main__":
    unittest.main()
