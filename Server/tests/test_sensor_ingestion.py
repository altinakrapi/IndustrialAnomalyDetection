import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dependencies import get_current_user, get_db
from app.modules.sensor_ingestion.api import (
    BatchReadingInput,
    SensorReadingInput,
    SensorReadingBatchInput,
    _check_rate_limit,
    _ingest_one,
    _new_device_key,
    _prepare_reading,
    authenticate_device_api_key,
    key_router,
    router as reading_router,
)
from app.modules.devices.application.measurement_recording import (
    record_equipment_measurement,
)
from app.shared.models import (
    AlertEpisode,
    AnalysisRun,
    Anomaly,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
)


class SensorReadingInputTests(unittest.TestCase):
    def test_rejects_non_finite_values(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                SensorReadingInput(
                    device_identifier="BEARING-1",
                    parameter="Vibration RMS",
                    value=value,
                    unit="mm/s",
                )

    def test_requires_timezone_for_sensor_timestamp(self) -> None:
        with self.assertRaises(ValidationError):
            SensorReadingInput(
                device_identifier="BEARING-1",
                parameter="Vibration RMS",
                value=1.2,
                unit="mm/s",
                sensor_timestamp=datetime(2026, 10, 27),
            )

    def test_batch_has_a_bounded_non_empty_reading_list(self) -> None:
        with self.assertRaises(ValidationError):
            BatchReadingInput(
                parameter="Vibration RMS",
                value=1.2,
                unit="mm/s",
                unknown="not accepted",
            )


class SensorIngestionSecurityTests(unittest.TestCase):
    def test_missing_bad_and_revoked_keys_are_rejected(self) -> None:
        db = Mock()
        db.scalar.return_value = None
        for key in (None, "bad-key", "revoked-key"):
            with self.subTest(key=key), self.assertRaises(HTTPException) as error:
                authenticate_device_api_key(key, db)
            self.assertEqual(error.exception.status_code, 401)

    def test_key_management_rejects_non_administrator(self) -> None:
        app = FastAPI()
        app.include_router(key_router, prefix="/keys")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=5, role="data_analyst"
        )
        app.dependency_overrides[get_db] = lambda: Mock()

        with TestClient(app) as client:
            response = client.post("/keys/devices/2")

        self.assertEqual(response.status_code, 403)

    @patch("app.modules.sensor_ingestion.api._ingest_one")
    def test_authenticated_sensor_post_records_through_shared_ingestion_path(
        self, ingest_one: Mock
    ) -> None:
        api_key = SimpleNamespace(
            id=4,
            device_id=2,
            revoked_at=None,
            last_used_at=None,
            rate_window_started_at=datetime.now(timezone.utc),
            rate_count=0,
        )
        db = Mock()
        db.scalar.side_effect = [
            api_key,
            SimpleNamespace(id=2),
            SimpleNamespace(id=3, name="Vibration RMS", unit="mm/s"),
            api_key,
        ]
        ingest_one.return_value = {
            "id": 8,
            "analysis_status": "completed",
            "duplicate": False,
        }
        app = FastAPI()
        app.include_router(reading_router, prefix="/readings")
        app.dependency_overrides[get_db] = lambda: db

        with TestClient(app) as client:
            response = client.post(
                "/readings",
                headers={"X-Device-API-Key": "valid-device-key"},
                json={
                    "device_identifier": "BEARING-1",
                    "parameter": "Vibration RMS",
                    "value": 2.8,
                    "unit": "mm/s",
                },
            )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertFalse(response.json()["duplicate"])
        ingest_one.assert_called_once()

    def test_batch_rejects_duplicate_parameter_timestamp_before_writing(self) -> None:
        from app.modules.sensor_ingestion.api import ingest_reading_batch

        device = SimpleNamespace(id=2)
        parameter = SimpleNamespace(id=3, name="Vibration RMS", unit="mm/s")
        api_key = SimpleNamespace(id=4)
        payload = SensorReadingBatchInput(
            device_identifier="BEARING-1",
            readings=[
                BatchReadingInput(parameter="Vibration RMS", value=2, unit="mm/s"),
                BatchReadingInput(parameter="Vibration RMS", value=3, unit="mm/s"),
            ],
        )
        with (
            patch(
                "app.modules.sensor_ingestion.api._authorized_device",
                return_value=device,
            ),
            patch(
                "app.modules.sensor_ingestion.api._prepare_reading",
                return_value=parameter,
            ),
            self.assertRaises(HTTPException) as error,
        ):
            ingest_reading_batch(payload, api_key, Mock())

        self.assertEqual(error.exception.status_code, 422)

    def test_created_key_is_returned_once_but_only_its_hash_is_stored(self) -> None:
        db = Mock()
        device = SimpleNamespace(id=2, source_type="simulated")
        administrator = SimpleNamespace(id=5)

        api_key, token = _new_device_key(db, device, administrator)

        self.assertTrue(token.startswith("iad_"))
        self.assertNotEqual(api_key.key_hash, token)
        self.assertEqual(len(api_key.key_hash), 64)
        self.assertEqual(api_key.key_prefix, token[:12])
        self.assertEqual(device.source_type, "simulated")


class SensorReadingValidationTests(unittest.TestCase):
    def test_rejects_negative_vibration_rms(self) -> None:
        parameter = SimpleNamespace(
            id=3,
            name="Vibration RMS",
            unit="mm/s",
        )
        db = Mock()
        db.scalar.return_value = parameter
        device = SimpleNamespace(id=2)
        reading = BatchReadingInput(
            parameter="Vibration RMS",
            value=-0.1,
            unit="mm/s",
        )

        with self.assertRaises(HTTPException) as error:
            _prepare_reading(reading, device, db)

        self.assertEqual(error.exception.status_code, 422)

    def test_rejects_temperature_below_absolute_zero(self) -> None:
        parameter = SimpleNamespace(
            id=3,
            name="Temperature",
            unit="°C",
        )
        db = Mock()
        db.scalar.return_value = parameter
        reading = BatchReadingInput(
            parameter="Temperature",
            value=-300,
            unit="°C",
        )

        with self.assertRaises(HTTPException) as error:
            _prepare_reading(reading, SimpleNamespace(id=2), db)

        self.assertEqual(error.exception.status_code, 422)

    @patch("app.modules.sensor_ingestion.api._measurement_payload")
    @patch("app.modules.devices.application.analysis.notify_user")
    @patch("app.modules.devices.application.analysis.notify_roles")
    def test_extreme_physically_possible_sensor_reading_is_stored_and_flagged(
        self,
        _notify_roles: Mock,
        _notify_user: Mock,
        measurement_payload: Mock,
    ) -> None:
        device = Device(
            id=2,
            name="Sensor bearing",
            device_code="SENSOR-2",
            device_type="bearing",
            source_type="manual",
            is_registered=True,
            responsible_user_id=7,
        )
        parameter = EquipmentParameter(
            id=3,
            device_id=2,
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
            flatline_reading_count=10,
            flatline_variance_tolerance=0.05,
            updated_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
            device=device,
        )
        reading = BatchReadingInput(
            parameter="Vibration RMS",
            value=1_000_000.0,
            unit="mm/s",
            idempotency_key="extreme-sensor-reading",
        )
        validation_db = Mock()
        validation_db.scalar.return_value = parameter
        self.assertIs(_prepare_reading(reading, device, validation_db), parameter)

        measurement_payload.return_value = {
            "id": 8,
            "value": 1_000_000.0,
            "source_type": "sensor",
        }
        db = Mock()
        db.scalar.side_effect = [None, None, None, None]
        db.get.return_value = None
        db.scalars.return_value = [0.8, 0.9, 1.0, 1.1, 0.9, 1.0, 1.1, 0.8]
        db.execute.return_value = []
        db.begin_nested.return_value = nullcontext()
        stored: list[EquipmentMeasurement] = []
        runs: list[AnalysisRun] = []
        anomalies: list[Anomaly] = []

        def persist(record: object) -> None:
            if isinstance(record, EquipmentMeasurement):
                record.id = 8
                record.parameter = parameter
                stored.append(record)
            elif isinstance(record, AnalysisRun):
                record.id = 12
                runs.append(record)
            elif isinstance(record, AlertEpisode):
                record.id = 31
            elif isinstance(record, Anomaly):
                record.id = 52
                anomalies.append(record)

        db.add.side_effect = persist
        received_at = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        response = _ingest_one(
            reading,
            device,
            parameter,
            SimpleNamespace(id=4),
            db,
            received_at,
        )

        self.assertEqual(response["analysis_status"], "completed")
        self.assertEqual(response["anomaly_count"], 1)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].value, 1_000_000.0)
        self.assertEqual(stored[0].source_type, "sensor")
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].metrics["model_result"]["is_anomaly"])
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].equipment_measurement_id, stored[0].id)

    def test_rejects_unit_mismatch(self) -> None:
        parameter = SimpleNamespace(
            id=3,
            name="Vibration RMS",
            unit="mm/s",
        )
        db = Mock()
        db.scalar.return_value = parameter
        reading = BatchReadingInput(
            parameter="Vibration RMS",
            value=1,
            unit="m/s",
        )

        with self.assertRaises(HTTPException) as error:
            _prepare_reading(reading, SimpleNamespace(id=2), db)

        self.assertEqual(error.exception.status_code, 422)

    def test_rejects_sensor_timestamps_outside_allowed_window(self) -> None:
        from app.modules.sensor_ingestion.api import _validate_sensor_timestamp

        now = datetime.now(timezone.utc)
        with self.assertRaises(HTTPException) as old_error:
            _validate_sensor_timestamp(now - timedelta(days=31), now)
        with self.assertRaises(HTTPException) as future_error:
            _validate_sensor_timestamp(now + timedelta(minutes=6), now)

        self.assertEqual(old_error.exception.status_code, 422)
        self.assertEqual(future_error.exception.status_code, 422)


class SensorIdempotencyAndRateLimitTests(unittest.TestCase):
    def test_retry_without_sensor_timestamp_reuses_idempotent_measurement(self) -> None:
        measured_at = datetime(2026, 10, 27, tzinfo=timezone.utc)
        existing = SimpleNamespace(
            id=8,
            value=2.8,
            source_type="sensor",
            measured_at=measured_at,
        )
        run = SimpleNamespace(id=12)
        db = Mock()
        db.scalar.side_effect = [existing, run]

        measurement, result_run, duplicate = record_equipment_measurement(
            db,
            SimpleNamespace(id=2),
            SimpleNamespace(id=3, unit="mm/s"),
            2.8,
            None,
            source_type="sensor",
            source="sensor",
            measured_at=measured_at + timedelta(seconds=3),
            received_at=measured_at + timedelta(seconds=3),
            idempotency_key="sensor:retry",
        )

        self.assertIs(measurement, existing)
        self.assertIs(result_run, run)
        self.assertTrue(duplicate)
        db.add.assert_not_called()

    def test_reusing_sensor_idempotency_key_with_different_sensor_time_conflicts(
        self,
    ) -> None:
        measured_at = datetime(2026, 10, 27, tzinfo=timezone.utc)
        existing = SimpleNamespace(
            id=8,
            value=2.8,
            source_type="sensor",
            measured_at=measured_at,
        )
        db = Mock()
        db.scalar.return_value = existing

        with self.assertRaises(ValueError):
            record_equipment_measurement(
                db,
                SimpleNamespace(id=2),
                SimpleNamespace(id=3, unit="mm/s"),
                2.8,
                None,
                source_type="sensor",
                source="sensor",
                measured_at=measured_at + timedelta(seconds=3),
                received_at=measured_at + timedelta(seconds=3),
                sensor_timestamp=measured_at + timedelta(seconds=3),
                idempotency_key="sensor:retry",
            )
        db.add.assert_not_called()

    @patch("app.modules.sensor_ingestion.api.record_equipment_measurement")
    @patch("app.modules.sensor_ingestion.api._measurement_payload")
    def test_same_reading_duplicate_is_returned_without_reanalysis(
        self, measurement_payload: Mock, record: Mock
    ) -> None:
        measurement = SimpleNamespace(id=8)
        run = SimpleNamespace(
            id=12, anomalies_found=0, status="completed", metrics={}
        )
        record.return_value = measurement, run, True
        measurement_payload.return_value = {"id": 8, "source_type": "simulated"}
        reading = BatchReadingInput(
            parameter="Vibration RMS",
            value=1.2,
            unit="mm/s",
            idempotency_key="reading-8",
        )

        response = _ingest_one(
            reading,
            SimpleNamespace(id=2, source_type="simulated"),
            SimpleNamespace(id=3),
            SimpleNamespace(id=4),
            Mock(),
            datetime.now(timezone.utc),
        )

        self.assertTrue(response["duplicate"])
        self.assertEqual(response["analysis_run_id"], 12)
        self.assertEqual(response["source_type"], "simulated")
        self.assertEqual(record.call_args.kwargs["source_type"], "simulated")

    @patch("app.modules.sensor_ingestion.api.record_equipment_measurement")
    @patch("app.modules.sensor_ingestion.api._measurement_payload")
    def test_physical_sensor_reading_retains_sensor_provenance(
        self, measurement_payload: Mock, record: Mock
    ) -> None:
        measurement = SimpleNamespace(id=8)
        run = SimpleNamespace(
            id=12, anomalies_found=0, status="completed", metrics={}
        )
        record.return_value = measurement, run, False
        measurement_payload.return_value = {"id": 8, "source_type": "sensor"}
        reading = BatchReadingInput(
            parameter="Vibration RMS",
            value=1.2,
            unit="mm/s",
            idempotency_key="reading-9",
        )

        response = _ingest_one(
            reading,
            SimpleNamespace(id=2, source_type="manual"),
            SimpleNamespace(id=3),
            SimpleNamespace(id=4),
            Mock(),
            datetime.now(timezone.utc),
        )

        self.assertEqual(response["source_type"], "sensor")
        self.assertEqual(record.call_args.kwargs["source_type"], "sensor")

    @patch("app.modules.sensor_ingestion.api.record_equipment_measurement")
    def test_conflicting_duplicate_returns_conflict(self, record: Mock) -> None:
        record.side_effect = ValueError("conflicting duplicate")
        reading = BatchReadingInput(
            parameter="Vibration RMS",
            value=1.2,
            unit="mm/s",
        )

        with self.assertRaises(HTTPException) as error:
            _ingest_one(
                reading,
                SimpleNamespace(id=2, source_type="manual"),
                SimpleNamespace(id=3),
                SimpleNamespace(id=4),
                Mock(),
                datetime.now(timezone.utc),
            )

        self.assertEqual(error.exception.status_code, 409)

    def test_per_device_rate_limit_rejects_excess_requests(self) -> None:
        now = datetime.now(timezone.utc)
        key = SimpleNamespace(
            id=4,
            rate_window_started_at=now - timedelta(seconds=10),
            rate_count=120,
            revoked_at=None,
        )
        db = Mock()
        db.scalar.return_value = key

        with self.assertRaises(HTTPException) as error:
            _check_rate_limit(db, key, now, 1)

        self.assertEqual(error.exception.status_code, 429)
        db.commit.assert_not_called()

    def test_per_device_rate_limit_reserves_capacity_before_ingestion(self) -> None:
        now = datetime.now(timezone.utc)
        key = SimpleNamespace(
            id=4,
            rate_window_started_at=now - timedelta(seconds=10),
            rate_count=8,
            revoked_at=None,
        )
        db = Mock()
        db.scalar.return_value = key
        db.refresh.return_value = None

        _check_rate_limit(db, key, now, 3)

        self.assertEqual(key.rate_count, 11)
        db.commit.assert_called_once()


class AnalysisFailurePersistenceTests(unittest.TestCase):
    @patch(
        "app.modules.devices.application.measurement_recording.notify_user"
    )
    @patch(
        "app.modules.devices.application.measurement_recording.notify_roles"
    )
    @patch(
        "app.modules.devices.application.measurement_recording.analyze_equipment_measurement",
        side_effect=RuntimeError("analysis engine unavailable"),
    )
    def test_analysis_failure_keeps_sensor_reading_and_failed_run(
        self, analysis: Mock, notify_roles: Mock, notify_user: Mock
    ) -> None:
        db = Mock()
        db.scalar.return_value = None
        db.begin_nested.return_value = MagicMock()
        device = SimpleNamespace(
            id=2,
            name="Bearing",
            responsible_user_id=None,
        )
        parameter = SimpleNamespace(
            id=3,
            name="Vibration RMS",
            unit="mm/s",
            updated_at=datetime.now(timezone.utc),
            analysis_method="isolation_forest",
            lower_limit=None,
            upper_limit=None,
            warning_low=None,
            warning_high=None,
            critical_low=None,
            critical_high=None,
            z_score_threshold=3.0,
            isolation_contamination=0.05,
        )

        def assign_primary_key() -> None:
            added = db.add.call_args.args[0]
            added.id = 8 if added.__class__.__name__ == "EquipmentMeasurement" else 9

        db.flush.side_effect = assign_primary_key
        measurement, run, duplicate = record_equipment_measurement(
            db,
            device,
            parameter,
            2.8,
            None,
            source_type="sensor",
            source="sensor",
            measured_at=datetime.now(timezone.utc),
            received_at=datetime.now(timezone.utc),
        )

        self.assertFalse(duplicate)
        self.assertEqual(measurement.source_type, "sensor")
        self.assertEqual(run.status, "failed")
        self.assertIn(measurement, [call.args[0] for call in db.add.call_args_list])
        db.commit.assert_called_once()
        analysis.assert_called_once()
        notify_roles.assert_called_once()
        notify_user.assert_called_once()


if __name__ == "__main__":
    unittest.main()
