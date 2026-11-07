from datetime import datetime, timedelta, timezone
import time
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user, get_db
from app.modules.anomaly_detection.api.router import (
    _anomaly_query,
    router as anomaly_router,
)
from app.modules.anomaly_detection.application.device_anomaly_service import (
    grouped_device_summary,
)
from app.shared.database.base import Base
from app.shared.models import (
    AnalysisRun,
    Anomaly,
    Dataset,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    User,
)


class DeviceGroupedAnomalyRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.analyst = User(
            full_name="Test Analyst",
            email="analyst@example.test",
            password_hash="not-used",
            role="data_analyst",
        )
        self.technician = User(
            full_name="Test Technician",
            email="technician@example.test",
            password_hash="not-used",
            role="maintenance_technician",
        )
        other_technician = User(
            full_name="Other Technician",
            email="other@example.test",
            password_hash="not-used",
            role="maintenance_technician",
        )
        self.db.add_all([self.analyst, self.technician, other_technician])
        self.db.flush()
        self.devices = [
            Device(
                name="Motor A",
                device_code="MOTOR-A",
                device_type="motor",
                source_type="manual",
                is_registered=True,
                responsible_user_id=self.technician.id,
            ),
            Device(
                name="Motor B",
                device_code="MOTOR-B",
                device_type="motor",
                source_type="sensor",
                is_registered=True,
                responsible_user_id=other_technician.id,
            ),
        ]
        self.db.add_all(self.devices)
        self.db.flush()
        self.parameters = [
            EquipmentParameter(
                device_id=device.id,
                name="Vibration RMS",
                unit="mm/s",
            )
            for device in self.devices
        ]
        self.db.add_all(self.parameters)
        self.db.flush()
        self._seed_equipment_rows()
        self._seed_dataset_anomaly()
        self.db.commit()
        self.current_user = self.analyst

        self.app = FastAPI()
        self.app.include_router(anomaly_router, prefix="/anomalies")

        def db_override():
            yield self.db

        def user_override():
            return self.current_user

        self.app.dependency_overrides[get_db] = db_override
        self.app.dependency_overrides[get_current_user] = user_override
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def _seed_equipment_rows(self) -> None:
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = []
        for index in range(56):
            detected_at = base + timedelta(hours=index)
            rows.append(
                (
                    self.devices[0],
                    self.parameters[0],
                    "simulated" if index % 3 == 0 else "manual",
                    "confirmed" if index % 2 else "new",
                    detected_at,
                    base + timedelta(hours=55 - index),
                )
            )
        for index in range(4):
            detected_at = base + timedelta(days=3, hours=index)
            rows.append(
                (
                    self.devices[1],
                    self.parameters[1],
                    "sensor",
                    "under_review" if index % 2 else "rejected",
                    detected_at,
                    detected_at,
                )
            )

        for device, parameter, source_type, status, detected_at, measured_at in rows:
            measurement = EquipmentMeasurement(
                parameter_id=parameter.id,
                value=2.0,
                unit="mm/s",
                measured_at=measured_at,
                source=source_type,
                source_type=source_type,
            )
            self.db.add(measurement)
            self.db.flush()
            run = AnalysisRun(
                device_id=device.id,
                equipment_measurement_id=measurement.id,
                execution_type="measurement",
                source_type="equipment",
                status="completed",
                metrics={},
            )
            self.db.add(run)
            self.db.flush()
            self.db.add(
                Anomaly(
                    analysis_run_id=run.id,
                    equipment_measurement_id=measurement.id,
                    anomaly_type="z_score",
                    severity="medium",
                    computed_severity="high" if status == "confirmed" else None,
                    status=status,
                    feature_values={"Vibration RMS": 2.0},
                    expected_range={},
                    detected_at=detected_at,
                )
            )
        first_measurement = self.db.scalar(
            select(EquipmentMeasurement)
            .where(EquipmentMeasurement.parameter_id == self.parameters[0].id)
            .order_by(EquipmentMeasurement.id)
            .limit(1)
        )
        extra_run = AnalysisRun(
            device_id=self.devices[0].id,
            equipment_measurement_id=first_measurement.id,
            execution_type="measurement",
            source_type="equipment",
            status="completed",
            metrics={},
        )
        self.db.add(extra_run)
        self.db.flush()
        self.db.add(
            Anomaly(
                analysis_run_id=extra_run.id,
                equipment_measurement_id=first_measurement.id,
                anomaly_type="isolation_forest",
                severity="medium",
                status="new",
                feature_values={"Vibration RMS": 2.0},
                expected_range={},
                detected_at=base,
            )
        )
        self.db.flush()

    def _seed_dataset_anomaly(self) -> None:
        dataset = Dataset(
            name="NASA IMS fixture",
            file_path="fixture.zip",
            file_format="nasa_ims",
        )
        self.db.add(dataset)
        self.db.flush()
        run = AnalysisRun(
            dataset_id=dataset.id,
            source_type="dataset",
            status="completed",
            metrics={},
        )
        self.db.add(run)
        self.db.flush()
        self.db.add(
            Anomaly(
                analysis_run_id=run.id,
                anomaly_type="isolation_forest",
                severity="high",
                status="new",
                feature_values={"bearing_1_rms": 4.2},
                expected_range={},
            )
        )
        self.db.flush()

    def _get(self, path: str, **params):
        response = self.client.get(path, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_device_counts_reconcile_with_flat_list_and_exclude_dataset(self) -> None:
        grouped = self._get(
            "/anomalies/devices/grouped",
            include_test_data="true",
        )
        flat = self._get(
            "/anomalies/page",
            include_test_data="true",
            limit=100,
        )
        raw_summary = self._get(
            "/anomalies/summary",
            include_test_data="true",
        )

        self.assertEqual(grouped["total"], flat["anomaly_row_total"])
        self.assertEqual(
            sum(device["anomaly_count"] for device in grouped["devices"]),
            flat["anomaly_row_total"],
        )
        self.assertEqual(grouped["total"], 61)
        self.assertEqual(grouped["measurement_total"], flat["total"])
        self.assertEqual(
            sum(device["measurement_count"] for device in grouped["devices"]),
            flat["total"],
        )
        self.assertEqual(raw_summary["total"], 61)
        self.assertEqual(raw_summary["unique_affected_measurements"], 60)
        self.assertEqual(
            {device["device_name"] for device in grouped["devices"]},
            {"Motor A", "Motor B"},
        )
        self.assertEqual(
            {device["anomaly_count"] for device in grouped["devices"]},
            {57, 4},
        )
        self.assertEqual(
            {device["measurement_count"] for device in grouped["devices"]},
            {56, 4},
        )

    def test_grouped_total_equals_sum_across_all_returned_devices(self) -> None:
        grouped = self._get(
            "/anomalies/devices/grouped",
            include_test_data="true",
        )

        self.assertEqual(len(grouped["devices"]), 2)
        self.assertEqual(
            grouped["total"],
            sum(device["anomaly_count"] for device in grouped["devices"]),
        )
        self.assertEqual(
            grouped["measurement_total"],
            sum(device["measurement_count"] for device in grouped["devices"]),
        )

    def test_filters_device_period_source_and_status(self) -> None:
        device_filtered = self._get(
            "/anomalies/devices/grouped",
            include_test_data="true",
            device_id=self.devices[0].id,
        )
        self.assertEqual(device_filtered["total"], 57)
        self.assertEqual(device_filtered["measurement_total"], 56)
        self.assertEqual(len(device_filtered["devices"]), 1)

        period_filtered = self._get(
            "/anomalies/devices/grouped",
            include_test_data="true",
            start_date="2026-01-03T00:00:00Z",
            end_date="2026-01-03T07:00:00Z",
        )
        self.assertEqual(period_filtered["total"], 8)

        source_filtered = self._get(
            "/anomalies/devices/grouped",
            include_test_data="true",
            measurement_source_type="simulated",
        )
        self.assertEqual(source_filtered["total"], 20)
        self.assertEqual(source_filtered["devices"][0]["source_types"], ["simulated"])

        status_filtered = self._get(
            "/anomalies/devices/grouped",
            include_test_data="true",
            status_filter="confirmed",
        )
        self.assertEqual(status_filtered["total"], 28)

    def test_grouped_and_flat_filters_return_the_same_population(self) -> None:
        source_filters = ("all", "manual", "sensor", "simulated")
        status_filters = (
            None,
            "new",
            "under_review",
            "confirmed",
            "rejected",
            "reviewed",
            "closed",
        )
        date_ranges = (
            (None, None),
            ("2026-01-03T00:00:00Z", "2026-01-03T07:00:00Z"),
        )

        for source_filter in source_filters:
            for status_filter in status_filters:
                for start_date, end_date in date_ranges:
                    with self.subTest(
                        source=source_filter,
                        status=status_filter,
                        start_date=start_date,
                        end_date=end_date,
                    ):
                        params = {
                            "source_type": "equipment",
                            "include_test_data": "true",
                            "include_dataset": "false",
                        }
                        if source_filter != "all":
                            params["measurement_source_type"] = source_filter
                        if status_filter is not None:
                            params["status_filter"] = status_filter
                        if start_date is not None:
                            params["start_date"] = start_date
                            params["end_date"] = end_date

                        grouped = self._get(
                            "/anomalies/devices/grouped",
                            **params,
                        )
                        flat = self._get(
                            "/anomalies/page",
                            **params,
                            limit=200,
                        )

                        self.assertEqual(
                            grouped["total"],
                            flat["anomaly_row_total"],
                        )
                        self.assertEqual(
                            grouped["measurement_total"],
                            flat["total"],
                        )
                        self.assertEqual(
                            sum(
                                device["anomaly_count"]
                                for device in grouped["devices"]
                            ),
                            flat["anomaly_row_total"],
                        )
                        self.assertEqual(
                            sum(
                                device["measurement_count"]
                                for device in grouped["devices"]
                            ),
                            flat["total"],
                        )

    def test_real_api_url_registers_and_serves_grouped_route(self) -> None:
        api_app = FastAPI()
        api_app.include_router(
            anomaly_router,
            prefix="/api/v1/anomalies",
        )

        def db_override():
            yield self.db

        def user_override():
            return self.current_user

        api_app.dependency_overrides[get_db] = db_override
        api_app.dependency_overrides[get_current_user] = user_override

        self.assertIn(
            "/api/v1/anomalies/devices/grouped",
            api_app.openapi()["paths"],
        )
        registered_paths = [route.path for route in anomaly_router.routes]
        self.assertLess(
            registered_paths.index("/devices/grouped"),
            registered_paths.index("/{anomaly_id}"),
        )
        with TestClient(api_app) as api_client:
            response = api_client.get(
                "/api/v1/anomalies/devices/grouped",
                params={
                    "source_type": "equipment",
                    "include_test_data": "true",
                    "include_dataset": "false",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 61)

    def test_device_page_applies_period_source_and_status_filters(self) -> None:
        device_id = self.devices[0].id
        period_filtered = self._get(
            f"/anomalies/devices/{device_id}/page",
            include_test_data="true",
            start_date="2026-01-03T00:00:00Z",
            end_date="2026-01-03T07:00:00Z",
        )
        source_filtered = self._get(
            f"/anomalies/devices/{device_id}/page",
            include_test_data="true",
            measurement_source_type="simulated",
        )
        status_filtered = self._get(
            f"/anomalies/devices/{device_id}/page",
            include_test_data="true",
            status_filter="confirmed",
        )

        self.assertEqual(period_filtered["total"], 8)
        self.assertEqual(source_filtered["total"], 19)
        self.assertEqual(status_filtered["total"], 28)

    def test_device_page_uses_50_item_pages_and_latest_anomaly_order(self) -> None:
        first_page = self._get(
            f"/anomalies/devices/{self.devices[0].id}/page",
            include_test_data="true",
            limit=50,
            offset=0,
        )
        last_page = self._get(
            f"/anomalies/devices/{self.devices[0].id}/page",
            include_test_data="true",
            limit=50,
            offset=50,
        )
        beyond_end = self._get(
            f"/anomalies/devices/{self.devices[0].id}/page",
            include_test_data="true",
            limit=50,
            offset=100,
        )

        self.assertEqual(first_page["total"], 56)
        self.assertEqual(len(first_page["items"]), 50)
        self.assertEqual(len(last_page["items"]), 6)
        self.assertEqual(beyond_end["items"], [])
        self.assertEqual(
            first_page["items"][0]["detected_at"],
            "2026-01-03T07:00:00",
        )
        self.assertGreater(
            first_page["items"][0]["detected_at"],
            first_page["items"][-1]["detected_at"],
        )
        self.assertEqual(
            first_page["items"][0]["measurement_time"],
            "2026-01-01T00:00:00",
        )

    def test_technician_is_scoped_to_authorised_devices(self) -> None:
        self.current_user = self.technician
        grouped = self._get(
            "/anomalies/devices/grouped", include_test_data="true"
        )
        allowed_page = self._get(
            f"/anomalies/devices/{self.devices[0].id}/page",
            include_test_data="true",
        )
        denied_page = self.client.get(
            f"/anomalies/devices/{self.devices[1].id}/page",
            params={"include_test_data": "true"},
        )
        denied_dataset_page = self.client.get(
            f"/anomalies/devices/{self.devices[1].id}/page",
            params={"source_type": "dataset"},
        )

        self.assertEqual(grouped["total"], 57)
        self.assertEqual(
            {device["device_id"] for device in grouped["devices"]},
            {self.devices[0].id},
        )
        self.assertEqual(allowed_page["total"], 56)
        self.assertEqual(denied_page.status_code, 403)
        self.assertEqual(denied_dataset_page.status_code, 403)

    def test_invalid_device_and_unauthenticated_requests(self) -> None:
        invalid = self.client.get("/anomalies/devices/99999/page")
        invalid_dataset = self.client.get(
            "/anomalies/devices/99999/page",
            params={"source_type": "dataset"},
        )
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(invalid_dataset.status_code, 404)

        unauthenticated_app = FastAPI()
        unauthenticated_app.include_router(anomaly_router, prefix="/anomalies")

        def db_override():
            yield self.db

        unauthenticated_app.dependency_overrides[get_db] = db_override
        with TestClient(unauthenticated_app) as unauthenticated_client:
            grouped_response = unauthenticated_client.get(
                "/anomalies/devices/grouped"
            )
            page_response = unauthenticated_client.get(
                f"/anomalies/devices/{self.devices[0].id}/page"
            )
        self.assertEqual(grouped_response.status_code, 401)
        self.assertEqual(page_response.status_code, 401)

    def test_grouped_summary_handles_ten_thousand_anomaly_rows(self) -> None:
        device_id = self.devices[0].id
        parameter_id = self.parameters[0].id
        base = datetime(2027, 1, 1)
        measurements = [
            {
                "id": 1000 + index,
                "parameter_id": parameter_id,
                "value": 2.0,
                "unit": "mm/s",
                "measured_at": base + timedelta(seconds=index),
                "source": "manual",
                "source_type": "manual",
            }
            for index in range(10_000)
        ]
        runs = [
            {
                "id": 1000 + index,
                "device_id": device_id,
                "equipment_measurement_id": 1000 + index,
                "execution_type": "measurement",
                "source_type": "equipment",
                "status": "completed",
                "metrics": {},
            }
            for index in range(10_000)
        ]
        anomalies = [
            {
                "id": 1000 + index,
                "analysis_run_id": 1000 + index,
                "equipment_measurement_id": 1000 + index,
                "anomaly_type": "z_score",
                "severity": "medium",
                "status": "new",
                "feature_values": {},
                "expected_range": {},
                "detected_at": base + timedelta(seconds=index),
            }
            for index in range(10_000)
        ]
        self.db.execute(insert(EquipmentMeasurement), measurements)
        self.db.execute(insert(AnalysisRun), runs)
        self.db.execute(insert(Anomaly), anomalies)
        self.db.commit()

        started = time.perf_counter()
        summary = grouped_device_summary(
            db=self.db,
            anomaly_query=_anomaly_query,
            user=self.analyst,
            filters={
                "source_type": "equipment",
                "include_test_data": True,
                "include_dataset": False,
                "measurement_source_type": None,
                "status_filter": None,
                "device_id": None,
                "dataset_id": None,
                "analysis_id": None,
                "severity": None,
                "algorithm": None,
                "parameter": None,
                "search": None,
                "start_date": None,
                "end_date": None,
            },
        )
        elapsed = time.perf_counter() - started

        print(f"10,000-row grouped-summary elapsed: {elapsed:.3f}s")
        self.assertGreaterEqual(summary["total"], 10_000)
        self.assertEqual(
            sum(device["anomaly_count"] for device in summary["devices"]),
            summary["total"],
        )


if __name__ == "__main__":
    unittest.main()
