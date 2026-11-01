import os
import unittest
import uuid

import simulator


@unittest.skipUnless(
    all(
        os.environ.get(name)
        for name in (
            "SIMULATOR_INTEGRATION_API_URL",
            "SIMULATOR_INTEGRATION_DEVICE_CODE",
            "SIMULATOR_INTEGRATION_DEVICE_KEY",
            "SIMULATOR_INTEGRATION_PARAMETER",
            "SIMULATOR_INTEGRATION_UNIT",
            "SIMULATOR_INTEGRATION_VALUE",
        )
    ),
    "Set simulator integration environment variables for a designated simulated device",
)
class SensorAPIIntegrationTests(unittest.TestCase):
    def test_sensor_reading_is_accepted_by_running_api(self) -> None:
        client = simulator.APIClient(
            os.environ["SIMULATOR_INTEGRATION_API_URL"],
            device_key=os.environ["SIMULATOR_INTEGRATION_DEVICE_KEY"],
        )
        result = client.post(
            "/api/v1/readings",
            {
                "device_identifier": os.environ[
                    "SIMULATOR_INTEGRATION_DEVICE_CODE"
                ],
                "parameter": os.environ["SIMULATOR_INTEGRATION_PARAMETER"],
                "value": float(os.environ["SIMULATOR_INTEGRATION_VALUE"]),
                "unit": os.environ["SIMULATOR_INTEGRATION_UNIT"],
                "idempotency_key": f"integration-{uuid.uuid4().hex}",
            },
        )
        self.assertEqual(result["source_type"], "sensor")
        self.assertEqual(result["source"], "sensor")
        self.assertIn("received_at", result)


if __name__ == "__main__":
    unittest.main()
