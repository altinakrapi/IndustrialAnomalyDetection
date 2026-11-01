import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import simulator


def small_config() -> dict:
    return {
        "api_base_url": "http://localhost:8000",
        "send_interval_seconds": 5,
        "duration_seconds": 10,
        "speed_up": 2,
        "seed": 17,
        "random_faults_per_device": 0,
        "random_fault_duration_seconds": 10,
        "max_delivery_attempts": 3,
        "devices": [
            {
                "name": "Sim Motor",
                "device_code": "SIM-MOTOR-TEST",
                "device_type": "motor",
                "operating_profile": "continuous",
                "baselines": {"Vibration RMS": 3.0},
                "noise": {"Vibration RMS": 0.3},
                "parameters": ["Vibration RMS"],
            }
        ],
        "faults": [],
    }


class ConfigTests(unittest.TestCase):
    def test_config_has_all_expected_parameter_units_and_bounds(self) -> None:
        for name, expected in {
            "Temperature": "°C",
            "Vibration RMS": "mm/s",
            "Pressure": "bar",
            "Current": "A",
            "Rotational Speed": "RPM",
        }.items():
            with self.subTest(parameter=name):
                payload = simulator._parameter_payload(name, {})
                self.assertEqual(payload["unit"], expected)
                self.assertGreater(payload["upper_limit"], payload["lower_limit"])
                self.assertEqual(payload["expected_interval_minutes"], 1)

    def test_rejects_duplicate_device_codes(self) -> None:
        config = small_config()
        config["devices"].append(dict(config["devices"][0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(simulator.SimulatorError):
                simulator.load_config(path)

    def test_rejects_unsupported_parameters_and_invalid_speed(self) -> None:
        config = small_config()
        config["devices"][0]["parameters"] = ["Unknown measurement"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(simulator.SimulatorError):
                simulator.load_config(path)

            config = small_config()
            config["speed_up"] = "fast"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(simulator.SimulatorError):
                simulator.load_config(path)


class ReproducibilityTests(unittest.TestCase):
    def test_same_seed_and_configuration_reproduce_samples_and_fault_schedule(self) -> None:
        config = small_config()
        config["random_faults_per_device"] = 2
        run_id = simulator.deterministic_run_id(config)
        first_faults = simulator.build_fault_schedule(config, run_id, 200)
        second_faults = simulator.build_fault_schedule(config, run_id, 200)
        self.assertEqual(first_faults, second_faults)

        device = config["devices"][0]
        first = simulator.SignalGenerator(config["seed"])
        second = simulator.SignalGenerator(config["seed"])
        first_samples = [
            first.sample(device, "Vibration RMS", tick * 10, first_faults)
            for tick in range(10)
        ]
        second_samples = [
            second.sample(device, "Vibration RMS", tick * 10, second_faults)
            for tick in range(10)
        ]
        self.assertEqual(first_samples, second_samples)


class FaultGroundTruthTests(unittest.TestCase):
    def test_every_fault_type_changes_or_omits_the_signal_in_its_interval(self) -> None:
        device = small_config()["devices"][0]
        device["noise"] = {"Vibration RMS": 2.0}
        fault_specs = [
            ("point_spike", 10, 10, 8),
            ("gradual_degradation", 0, 10, 0.8),
            ("sudden_level_shift", 0, 10, 8),
            ("stuck_sensor", 0, 10, 1),
            ("signal_dropout", 0, 10, 1),
            ("increased_variance", 0, 10, 20),
            ("slow_drift", 0, 10, 8),
        ]
        for fault_type, start, end, magnitude in fault_specs:
            with self.subTest(fault_type=fault_type):
                normal = simulator.SignalGenerator(31).sample(
                    device, "Vibration RMS", 10, []
                )[0]
                fault = {
                    "fault_id": "fault-1",
                    "device_code": device["device_code"],
                    "parameter": "Vibration RMS",
                    "type": fault_type,
                    "start_seconds": start,
                    "end_seconds": end,
                    "magnitude": magnitude,
                    "severity": "high",
                }
                generator = simulator.SignalGenerator(31)
                if fault_type == "stuck_sensor":
                    first, first_ids = generator.sample(
                        device, "Vibration RMS", 0, [fault]
                    )
                    second, second_ids = generator.sample(
                        device, "Vibration RMS", 10, [fault]
                    )
                    self.assertEqual(first_ids, ["fault-1"])
                    self.assertEqual(second_ids, ["fault-1"])
                    self.assertEqual(first, second)
                    continue
                result, active_ids = generator.sample(
                    device, "Vibration RMS", 10, [fault]
                )
                self.assertEqual(active_ids, ["fault-1"])
                if fault_type == "signal_dropout":
                    self.assertIsNone(result)
                else:
                    self.assertNotEqual(result, normal)

    def test_point_spike_changes_only_its_labeled_instant(self) -> None:
        device = small_config()["devices"][0]
        fault = {
            "fault_id": "spike-1",
            "device_code": device["device_code"],
            "parameter": "Vibration RMS",
            "type": "point_spike",
            "start_seconds": 10,
            "end_seconds": 10,
            "magnitude": 10,
            "severity": "high",
        }
        baseline_at_spike = simulator.SignalGenerator(1).sample(
            device, "Vibration RMS", 10, []
        )[0]
        injected_at_spike = simulator.SignalGenerator(1).sample(
            device, "Vibration RMS", 10, [fault]
        )[0]
        outside = simulator.SignalGenerator(1).sample(
            device, "Vibration RMS", 15, [fault]
        )[1]
        self.assertEqual(injected_at_spike - baseline_at_spike, 10)
        self.assertEqual(outside, [])


class APIClientTests(unittest.TestCase):
    def test_authentication_headers_are_sent_without_exposing_them_in_logs(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return b'{"ok": true}'

        def opener(request, timeout):
            captured["authorization"] = request.get_header("Authorization")
            captured["device_key"] = request.get_header("X-device-api-key")
            captured["timeout"] = timeout
            return FakeResponse()

        client = simulator.APIClient(
            "http://localhost:8000",
            bearer_token="admin-token",
            device_key="device-secret",
            opener=opener,
        )
        self.assertEqual(client.get("/health"), {"ok": True})
        self.assertEqual(captured["authorization"], "Bearer admin-token")
        self.assertEqual(captured["device_key"], "device-secret")
        self.assertEqual(captured["timeout"], 15)


class SetupTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {
            "SIMULATOR_ADMIN_EMAIL": "admin@example.test",
            "SIMULATOR_ADMIN_PASSWORD": "safe-test-password",
        },
    )
    @patch("simulator.APIClient")
    def test_setup_is_idempotent_and_stores_keys_locally(
        self, client_class
    ) -> None:
        config = small_config()

        class FakeAPI:
            def __init__(self, base_url: str, **kwargs) -> None:
                self.base_url = base_url
                self.bearer_token = kwargs.get("bearer_token")
                self.devices = []
                self.parameters = {}
                self.api_keys = {}
                self.device_creations = 0
                self.parameter_creations = 0
                self.key_creations = 0

            def post(self, path: str, payload: dict):
                if path == "/api/v1/auth/login":
                    return {"access_token": "admin-token"}
                if path == "/api/v1/devices":
                    self.device_creations += 1
                    device = {
                        "id": 2,
                        "device_code": payload["device_code"],
                        "device_type": payload["device_type"],
                        "source_type": payload["source_type"],
                    }
                    self.devices.append(device)
                    return device
                if path.endswith("/parameters"):
                    self.parameter_creations += 1
                    device_id = 2
                    parameter = {
                        "name": payload["name"],
                        "unit": payload["unit"],
                    }
                    self.parameters.setdefault(device_id, []).append(parameter)
                    return parameter
                if path.endswith("/device-api-keys/devices/2"):
                    self.key_creations += 1
                    key = "iad-secret-key-token"
                    self.api_keys[2] = [
                        {"key_prefix": key[:12], "revoked_at": None}
                    ]
                    return {"api_key": key}
                self.fail(f"Unexpected POST {path}")

            def get(self, path: str):
                if path == "/api/v1/auth/me":
                    return {"role": "administrator"}
                if path == "/api/v1/devices?limit=500&offset=0":
                    return self.devices
                if path.endswith("/parameters"):
                    return self.parameters.get(2, [])
                if path.endswith("/device-api-keys/devices/2"):
                    return self.api_keys.get(2, [])
                self.fail(f"Unexpected GET {path}")

        fake_admin = FakeAPI(config["api_base_url"], bearer_token="admin-token")
        anonymous = FakeAPI(config["api_base_url"])
        client_class.side_effect = [anonymous, fake_admin, anonymous, fake_admin]
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "devices.local.json"
            first_state = simulator.setup_devices(config, state_path)
            second_state = simulator.setup_devices(config, state_path)
            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(fake_admin.device_creations, 1)
        self.assertEqual(fake_admin.parameter_creations, 1)
        self.assertEqual(fake_admin.key_creations, 1)
        self.assertEqual(first_state, second_state)
        self.assertEqual(
            persisted["devices"]["SIM-MOTOR-TEST"]["api_key"],
            "iad-secret-key-token",
        )


class DeliveryTests(unittest.TestCase):
    def test_429_uses_retry_after_and_retries_same_idempotent_batch(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = []

            def post(self, path: str, payload: dict):
                self.calls.append((path, payload))
                if len(self.calls) == 1:
                    raise simulator.APIError(
                        429, "rate limited", {"retry-after": "4"}
                    )
                return {"count": 1, "readings": [{"id": 17}]}

        client = FakeClient()
        sleeps = []
        retry_events = []
        limiter = simulator.PerDeviceRateLimiter(
            limit=120,
            clock=lambda: 0,
            sleeper=sleeps.append,
        )
        reading = {
            "parameter": "Vibration RMS",
            "value": 2.2,
            "unit": "mm/s",
            "idempotency_key": "sim-run-00000001",
        }
        response = simulator.deliver_batch(
            client,
            device_code="SIM-1",
            readings=[reading],
            max_attempts=3,
            rate_limiter=limiter,
            sleeper=sleeps.append,
            on_retry=retry_events.append,
        )

        self.assertEqual(response["count"], 1)
        self.assertEqual(sleeps, [4])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0], client.calls[1])
        self.assertEqual(
            retry_events,
            [
                {
                    "event": "delivery_retry",
                    "device_code": "SIM-1",
                    "attempt": 1,
                    "error_type": "http",
                    "status_code": 429,
                    "error": "rate limited",
                    "retry_delay_seconds": 4,
                }
            ],
        )

    @patch("simulator.APIClient")
    def test_sample_count_finishes_after_wall_duration_expires(self, client_class) -> None:
        config = small_config()
        config["send_interval_seconds"] = 0.5
        config["duration_seconds"] = 1
        config["speed_up"] = 1
        config["devices"][0]["sample_count"] = 3
        clock_value = [0.0]

        class FakeAPI:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def post(self, _path, payload):
                clock_value[0] += 0.6
                return {
                    "readings": [
                        {
                            "id": index + 1,
                            "idempotency_key": reading["idempotency_key"],
                        }
                        for index, reading in enumerate(payload["readings"])
                    ]
                }

        client_class.return_value = FakeAPI()

        def fake_sleep(seconds: float) -> None:
            clock_value[0] += seconds

        with tempfile.TemporaryDirectory() as directory:
            ground_truth_path = Path(directory) / "ground_truth.jsonl"
            simulator.run_simulation(
                config,
                {"devices": {"SIM-MOTOR-TEST": {"api_key": "test-key"}}},
                ground_truth_path=ground_truth_path,
                clock=lambda: clock_value[0],
                sleeper=fake_sleep,
            )
            events = [
                json.loads(line)
                for line in ground_truth_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertGreater(clock_value[0], config["duration_seconds"])
        self.assertEqual(
            sum(event["event"] == "reading_accepted" for event in events),
            config["devices"][0]["sample_count"],
        )
        self.assertEqual(
            sum(event["event"] == "reading_generated" for event in events),
            config["devices"][0]["sample_count"],
        )

    def test_client_error_is_logged_and_not_retried(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = 0

            def post(self, *_):
                self.calls += 1
                raise simulator.APIError(422, "invalid parameter")

        client = FakeClient()
        sleeps = []
        limiter = simulator.PerDeviceRateLimiter(
            clock=lambda: 0,
            sleeper=lambda delay: sleeps.append(delay),
        )
        with self.assertLogs("sensor_simulator", level="ERROR"):
            with self.assertRaises(simulator.APIError):
                simulator.deliver_batch(
                    client,
                    device_code="SIM-1",
                    readings=[{"parameter": "bad"}],
                    max_attempts=5,
                    rate_limiter=limiter,
                    sleeper=sleeps.append,
                )
        self.assertEqual(client.calls, 1)

    def test_temporary_network_failure_uses_exponential_backoff(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = 0

            def post(self, *_):
                self.calls += 1
                if self.calls == 1:
                    raise simulator.SimulatorError("connection timed out")
                return {"count": 1, "readings": [{"id": 18}]}

        client = FakeClient()
        sleeps = []
        limiter = simulator.PerDeviceRateLimiter(
            clock=lambda: 0,
            sleeper=sleeps.append,
        )
        response = simulator.deliver_batch(
            client,
            device_code="SIM-1",
            readings=[{"parameter": "Current", "value": 2, "unit": "A"}],
            max_attempts=3,
            rate_limiter=limiter,
            sleeper=sleeps.append,
        )

        self.assertEqual(response["readings"][0]["id"], 18)
        self.assertEqual(client.calls, 2)
        self.assertEqual(sleeps, [1])

    def test_rate_limiter_waits_before_exceeding_device_limit(self) -> None:
        class FakeTime:
            now = 0.0
            waits = []

            @classmethod
            def sleep(cls, seconds: float) -> None:
                cls.waits.append(seconds)
                cls.now += seconds

        limiter = simulator.PerDeviceRateLimiter(
            limit=5,
            clock=lambda: FakeTime.now,
            sleeper=FakeTime.sleep,
        )
        limiter.reserve("SIM-1", 5)
        limiter.reserve("SIM-1", 1)

        self.assertEqual(FakeTime.waits, [60])

    @patch("simulator.APIClient")
    def test_generated_reading_is_preserved_when_api_rejects_delivery(
        self, client_class
    ) -> None:
        class FailingAPI:
            def __init__(
                self, base_url: str, *, device_key: str | None = None
            ) -> None:
                self.base_url = base_url
                self.device_key = device_key

            def post(self, *_):
                raise simulator.APIError(422, "rejected")

        client_class.return_value = FailingAPI(
            "http://localhost:8000", device_key="local-secret"
        )
        config = small_config()
        config["duration_seconds"] = 1
        config["max_delivery_attempts"] = 1
        state = {
            "api_base_url": config["api_base_url"],
            "devices": {
                "SIM-MOTOR-TEST": {"device_id": 2, "api_key": "local-secret"}
            },
        }

        class FakeTime:
            now = 0.0

            @classmethod
            def sleep(cls, seconds: float) -> None:
                cls.now += seconds

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "truth.jsonl"
            simulator.run_simulation(
                config,
                state,
                ground_truth_path=output,
                clock=lambda: FakeTime.now,
                sleeper=FakeTime.sleep,
                rate_limiter=simulator.PerDeviceRateLimiter(
                    clock=lambda: FakeTime.now,
                    sleeper=FakeTime.sleep,
                ),
            )
            events = [
                json.loads(line)["event"]
                for line in output.read_text(encoding="utf-8").splitlines()
            ]
            ledger = output.read_text(encoding="utf-8")

        self.assertIn("reading_generated", events)
        self.assertIn("delivery_failed", events)
        self.assertNotIn("local-secret", ledger)


if __name__ == "__main__":
    unittest.main()
