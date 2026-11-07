import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.dashboard.api.router import dashboard
from app.shared.database.base import Base
from app.shared.models import (
    AnalysisRun,
    Anomaly,
    Dataset,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    MeasurementRecord,
)


class DashboardAnomalyVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        device = Device(
            name="Simulated pump",
            device_code="SIM-DASHBOARD-01",
            device_type="pump",
            source_type="simulated",
            is_registered=True,
        )
        self.db.add(device)
        self.db.flush()
        parameter = EquipmentParameter(
            device_id=device.id,
            name="Vibration RMS",
            unit="mm/s",
            enabled=False,
        )
        self.db.add(parameter)
        self.db.flush()
        measurement = EquipmentMeasurement(
            parameter_id=parameter.id,
            value=2.4,
            unit="mm/s",
            measured_at=datetime(2026, 10, 28, tzinfo=timezone.utc),
            source="simulated",
            source_type="simulated",
        )
        dataset = Dataset(
            name="Unlinked NASA IMS dataset",
            file_path="nasa-ims.zip",
            file_format="nasa_ims",
        )
        self.db.add_all([measurement, dataset])
        self.db.flush()
        record = MeasurementRecord(
            dataset_id=dataset.id,
            source_row_number=1,
            values={"bearing_1_rms": 4.2},
        )
        self.db.add(record)
        self.db.flush()

        device_run = AnalysisRun(
            device_id=device.id,
            equipment_measurement_id=measurement.id,
            execution_type="measurement",
            source_type="equipment",
            status="completed",
            metrics={},
        )
        dataset_run = AnalysisRun(
            dataset_id=dataset.id,
            source_type="dataset",
            status="completed",
            metrics={},
        )
        self.db.add_all([device_run, dataset_run])
        self.db.flush()
        self.db.add_all(
            [
                Anomaly(
                    analysis_run_id=device_run.id,
                    equipment_measurement_id=measurement.id,
                    anomaly_type="z_score",
                    severity="medium",
                    status="new",
                    feature_values={"Vibration RMS": 2.4},
                    expected_range={},
                ),
                Anomaly(
                    analysis_run_id=dataset_run.id,
                    measurement_record_id=record.id,
                    anomaly_type="isolation_forest",
                    severity="high",
                    status="new",
                    feature_values={"bearing_1_rms": 4.2},
                    expected_range={},
                ),
            ]
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_dashboard_excludes_unlinked_dataset_anomalies_by_default(self) -> None:
        payload = dashboard(SimpleNamespace(role="data_analyst"), self.db)

        self.assertEqual(payload["metrics"]["anomalies"], 1)
        self.assertEqual(payload["metrics"]["unreviewed_anomalies"], 1)
        self.assertEqual(payload["source_breakdown"]["cases_to_review"]["dataset"], 0)
        self.assertEqual(
            [(item["source_type"], item["count"]) for item in payload["anomalies_by_device"]],
            [("simulated", 1)],
        )

    def test_dashboard_includes_dataset_anomalies_when_requested(self) -> None:
        payload = dashboard(
            user=SimpleNamespace(role="data_analyst"),
            db=self.db,
            include_dataset=True,
        )

        self.assertEqual(payload["metrics"]["anomalies"], 2)
        self.assertEqual(payload["metrics"]["unreviewed_anomalies"], 2)
        self.assertEqual(payload["source_breakdown"]["cases_to_review"]["dataset"], 1)
        chart_rows = {
            item["source_type"]: item["count"]
            for item in payload["anomalies_by_device"]
        }
        self.assertEqual(chart_rows, {"simulated": 1, "dataset": 1})
