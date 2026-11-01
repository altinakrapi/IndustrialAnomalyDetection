import copy
import json
import math
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import simulator


ALGORITHMS = (
    "isolation_forest",
    "z_score",
    "local_outlier_factor",
    "one_class_svm",
    "autoencoder",
    "ewma",
    "cusum",
)
FAULT_TYPES = (
    "point_spike",
    "gradual_degradation",
    "sudden_level_shift",
    "stuck_sensor",
    "signal_dropout",
    "increased_variance",
    "slow_drift",
)
WARMUP_READINGS = 80
TICK_SECONDS = 0.51
ANALYSIS_WINDOW = 100
REFIT_EVERY = 25


class APIClient:
    def __init__(self, base_url: str, bearer_token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token

    def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
    ) -> Any:
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, allow_nan=False).encode("utf-8")
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read()
                return json.loads(content.decode("utf-8")) if content else None
        except urllib.error.HTTPError as error:
            content = error.read()
            try:
                detail = json.loads(content.decode("utf-8")).get(
                    "detail", f"HTTP {error.code}"
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                detail = f"HTTP {error.code}"
            raise AssertionError(f"{method} {path} failed: {error.code} {detail}") from error


def detector_parameters(algorithm: str, seed: int) -> dict[str, Any]:
    if algorithm == "isolation_forest":
        return {
            "n_estimators": 20,
            "contamination": "auto",
            "random_state": seed,
        }
    if algorithm == "z_score":
        return {"threshold": 3.0, "random_state": seed}
    if algorithm == "local_outlier_factor":
        return {
            "n_neighbors": 10,
            "refit_every": REFIT_EVERY,
            "training_window": ANALYSIS_WINDOW,
            "random_state": seed,
        }
    if algorithm == "one_class_svm":
        return {
            "nu": 0.05,
            "gamma": "scale",
            "kernel": "rbf",
            "refit_every": REFIT_EVERY,
            "training_window": ANALYSIS_WINDOW,
            "random_state": seed,
        }
    if algorithm == "autoencoder":
        return {
            "hidden_layer_sizes": [8, 4],
            "max_iter": 50,
            "alpha": 0.0001,
            "threshold_percentile": 99.0,
            "refit_every": REFIT_EVERY,
            "training_window": ANALYSIS_WINDOW,
            "random_state": seed,
        }
    if algorithm == "ewma":
        return {
            "lambda": 0.2,
            "L": 3.0,
            "baseline_window": 30,
            "reset_rule": "signal",
        }
    if algorithm == "cusum":
        return {
            "k": 0.5,
            "h": 5.0,
            "baseline_window": 30,
            "reset_rule": "signal",
        }
    if algorithm == "ensemble":
        members = [
            {
                "algorithm": member_algorithm,
                "parameters": detector_parameters(member_algorithm, seed),
                "features": ["Vibration RMS"],
                "weight": 1.0,
            }
            for member_algorithm in ALGORITHMS
        ]
        return {
            "members": members,
            "voting_mode": "quorum",
            "quorum": 2,
            "high_confidence_threshold": 0.75,
        }
    raise AssertionError(f"Unknown detector {algorithm}")


def integration_seed(configured_seed: int) -> int:
    value = os.environ.get("SIMULATOR_SEED")
    if value is None:
        return configured_seed
    try:
        seed = int(value)
    except ValueError as error:
        raise AssertionError("SIMULATOR_SEED must be an integer") from error
    if seed < 0:
        raise AssertionError("SIMULATOR_SEED must be non-negative")
    return seed


def merge_integration_config(
    base_config: dict[str, Any],
    template: dict[str, Any],
    local_state: dict[str, Any],
) -> dict[str, Any]:
    if len(base_config["devices"]) < len(template["devices"]):
        raise AssertionError("config.json must provision three dedicated simulator devices")
    template_codes = [
        item["device_code"] for item in template["devices"]
    ]
    devices = []
    code_map: dict[str, str] = {}
    for template_device, configured_device in zip(
        template["devices"], base_config["devices"], strict=False
    ):
        code = configured_device["device_code"]
        if code not in local_state.get("devices", {}):
            raise AssertionError(
                f"devices.local.json has no provisioned key entry for {code}"
            )
        if not local_state["devices"][code].get("api_key"):
            raise AssertionError(f"devices.local.json has no API key for {code}")
        if "Vibration RMS" not in configured_device.get("parameters", []):
            raise AssertionError(
                f"Configured device {code} must have the Vibration RMS parameter"
            )
        runtime_device = copy.deepcopy(configured_device)
        runtime_device["parameters"] = ["Vibration RMS"]
        runtime_device["sample_count"] = template_device["sample_count"]
        runtime_device["parameter_intervals_seconds"] = copy.deepcopy(
            template_device.get("parameter_intervals_seconds", {})
        )
        devices.append(runtime_device)
        code_map[template_device["device_code"]] = code

    result = copy.deepcopy(template)
    result["api_base_url"] = base_config["api_base_url"]
    result["devices"] = devices
    result["faults"] = [
        {
            **fault,
            "device_code": code_map[fault["device_code"]],
        }
        for fault in template["faults"]
    ]
    if set(code_map) != set(template_codes):
        raise AssertionError("Integration device mapping is incomplete")
    return result


def equipment_parameter_update_payload(
    parameter: dict[str, Any],
    *,
    expected_interval_seconds: int | None,
) -> dict[str, Any]:
    fields = (
        "name",
        "unit",
        "lower_limit",
        "upper_limit",
        "warning_low",
        "warning_high",
        "critical_low",
        "critical_high",
        "expected_interval_minutes",
        "analysis_method",
        "z_score_threshold",
        "isolation_contamination",
        "consecutive_anomaly_threshold",
        "persistence_window_size",
        "persistence_anomaly_threshold",
        "flatline_reading_count",
        "flatline_variance_tolerance",
        "enabled",
    )
    payload = {field: parameter.get(field) for field in fields}
    payload["expected_interval_seconds"] = expected_interval_seconds
    return payload


def read_ground_truth(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def delivery_status_summary(
    events: list[dict[str, Any]], device_codes: list[str]
) -> dict[str, dict[str, Any]]:
    summary = {
        device_code: {
            "generated": 0,
            "accepted": 0,
            "accepted_by_status": Counter(),
            "omitted": 0,
            "retries_by_status": Counter(),
            "failures_by_status": Counter(),
        }
        for device_code in device_codes
    }
    for event in events:
        device_code = event.get("device_code")
        if device_code not in summary:
            continue
        item = summary[device_code]
        event_type = event.get("event")
        if event_type == "reading_generated":
            item["generated"] += 1
        elif event_type == "reading_accepted":
            item["accepted"] += 1
            status = event.get("http_status")
            item["accepted_by_status"][
                f"HTTP {status}" if status is not None else "unspecified"
            ] += 1
        elif event_type == "reading_omitted":
            item["omitted"] += 1
        elif event_type == "delivery_retry":
            status = event.get("status_code")
            item["retries_by_status"][
                f"HTTP {status}" if status is not None else event["error_type"]
            ] += 1
        elif event_type == "delivery_failed":
            status = event.get("status_code")
            item["failures_by_status"][
                f"HTTP {status}" if status is not None else event["error_type"]
            ] += 1
    return summary


def analysis_results(client: APIClient, analysis_id: int) -> dict[int, dict[str, Any]]:
    response = client.request(
        "GET",
        f"/api/v1/analyses/{analysis_id}/results?limit=1000&offset=0",
    )
    return {
        int(item["equipment_measurement_id"]): item
        for item in response["results"]
        if item.get("equipment_measurement_id") is not None
    }


def wait_for_analysis(
    client: APIClient,
    analysis_id: int,
    *,
    timeout_seconds: float = 240,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        analysis = client.request("GET", f"/api/v1/analyses/{analysis_id}")
        if analysis["status"] == "completed":
            return analysis
        if analysis["status"] == "failed":
            raise AssertionError(
                f"Analysis {analysis_id} failed: {analysis.get('error_message')}"
            )
        time.sleep(0.5)
    raise AssertionError(f"Analysis {analysis_id} did not finish in time")


@unittest.skipUnless(
    os.environ.get("SIMULATOR_FULL_INTEGRATION") == "1",
    "Set SIMULATOR_FULL_INTEGRATION=1 to run the live simulator/API integration test",
)
class FullSensorAnalysisIntegrationTests(unittest.TestCase):
    def test_seeded_simulation_all_detectors_ensemble_and_comparison(self) -> None:
        simulator_dir = Path(__file__).resolve().parents[1]
        base_config_path = simulator_dir / "config.json"
        local_state_path = simulator_dir / "devices.local.json"
        template_path = simulator_dir / "config.integration.json"
        for path in (base_config_path, local_state_path, template_path):
            self.assertTrue(path.is_file(), f"Required integration file missing: {path.name}")
        self.assertTrue(
            os.environ.get("SIMULATOR_ADMIN_EMAIL"),
            "Set SIMULATOR_ADMIN_EMAIL for the analysis API.",
        )
        self.assertTrue(
            os.environ.get("SIMULATOR_ADMIN_PASSWORD"),
            "Set SIMULATOR_ADMIN_PASSWORD for the analysis API.",
        )

        base_config = simulator.load_config(base_config_path)
        template_config = simulator.load_config(template_path)
        local_state = simulator._read_json(local_state_path)
        config = merge_integration_config(base_config, template_config, local_state)
        seed = integration_seed(int(config["seed"]))
        config["seed"] = seed
        if base_config["api_base_url"].rstrip("/") != (
            local_state.get("api_base_url", base_config["api_base_url"]).rstrip("/")
        ):
            raise AssertionError(
                "config.json and devices.local.json target different API URLs"
            )

        api_root = base_config["api_base_url"].rstrip("/")
        anonymous_client = APIClient(api_root)
        login = anonymous_client.request(
            "POST",
            "/api/v1/auth/login",
            {
                "email": os.environ["SIMULATOR_ADMIN_EMAIL"],
                "password": os.environ["SIMULATOR_ADMIN_PASSWORD"],
            },
        )
        client = APIClient(api_root, login["access_token"])
        current_user = client.request("GET", "/api/v1/auth/me")
        self.assertIn(current_user["role"], {"administrator", "data_analyst"})
        self.assertEqual(
            anonymous_client.request("GET", "/health")["status"],
            "ok",
        )

        catalog = {
            item["algorithm"]: item
            for item in client.request("GET", "/api/v1/analyses/detectors")
        }
        expected_algorithms = {*ALGORITHMS, "ensemble"}
        self.assertTrue(expected_algorithms.issubset(catalog))
        minimum_history = {
            algorithm: int(catalog[algorithm]["minimum_history"])
            for algorithm in sorted(expected_algorithms)
        }
        largest_minimum = max(minimum_history.values())
        self.assertGreaterEqual(
            WARMUP_READINGS,
            math.ceil(largest_minimum * 1.2),
            "The simulator warm-up must be at least the largest detector minimum plus 20%.",
        )
        print(f"Registry minimum history: {minimum_history}")
        print(
            "Test settings: warm-up=80; training_window=100; refit_every=25; "
            "control-chart baseline_window=30; ensemble quorum=2; "
            "expected_interval=3s (stale threshold=9s); send interval=0.75s; "
            f"seed={seed}; main ticks=492; auxiliary device ticks=160."
        )

        devices = client.request("GET", "/api/v1/devices?limit=500&offset=0")
        devices_by_code = {device["device_code"]: device for device in devices}
        configured_devices = config["devices"]
        device_rows: dict[str, tuple[dict, dict]] = {}
        for configured_device in configured_devices:
            device_code = configured_device["device_code"]
            device = devices_by_code.get(device_code)
            self.assertIsNotNone(device, f"Device {device_code} was not provisioned")
            self.assertEqual(device["source_type"], "simulated")
            parameters = client.request(
                "GET",
                f"/api/v1/devices/{device['id']}/parameters",
            )
            parameter = next(
                (
                    value
                    for value in parameters
                    if value["name"] == "Vibration RMS" and value["enabled"]
                ),
                None,
            )
            self.assertIsNotNone(
                parameter,
                f"{device_code} must have an enabled Vibration RMS parameter",
            )
            device_rows[device_code] = (device, parameter)

        for template_device, configured_device in zip(
            template_config["devices"], configured_devices, strict=True
        ):
            device_code = configured_device["device_code"]
            device, parameter = device_rows[device_code]
            old_interval_seconds = parameter.get("expected_interval_seconds")
            temporary_interval_seconds = template_device.get(
                "parameter_intervals_seconds", {}
            ).get("Vibration RMS")
            if temporary_interval_seconds is None:
                raise AssertionError(
                    "Integration config must set a short expected interval in seconds."
                )
            parameter_path = (
                f"/api/v1/devices/{device['id']}/parameters/{parameter['id']}"
            )
            client.request(
                "PATCH",
                parameter_path,
                equipment_parameter_update_payload(
                    parameter,
                    expected_interval_seconds=temporary_interval_seconds,
                ),
            )
            self.addCleanup(
                client.request,
                "PATCH",
                parameter_path,
                equipment_parameter_update_payload(
                    parameter,
                    expected_interval_seconds=old_interval_seconds,
                ),
            )

        run_id = f"it-{time.time_ns()}"
        run_directory = simulator_dir / "runs"
        run_directory.mkdir(parents=True, exist_ok=True)
        ground_truth_path = run_directory / f"full-integration-{run_id}.jsonl"
        simulator_log_path = run_directory / f"full-integration-{run_id}.log"
        runtime_config_path = run_directory / f"full-integration-{run_id}.config.json"
        self.assertFalse(ground_truth_path.exists())
        self.assertFalse(simulator_log_path.exists())
        self.assertFalse(runtime_config_path.exists())
        runtime_config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        environment = os.environ.copy()
        environment["SIMULATOR_LOG_LEVEL"] = "WARNING"
        started = time.perf_counter()
        simulation = subprocess.run(
            [
                sys.executable,
                str(simulator_dir / "simulator.py"),
                "--config",
                str(runtime_config_path),
                "--state",
                str(local_state_path),
                "run",
                "--run-id",
                run_id,
                "--ground-truth",
                str(ground_truth_path),
            ],
            cwd=simulator_dir,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        simulation_seconds = time.perf_counter() - started
        simulator_log_path.write_text(
            simulation.stdout + simulation.stderr,
            encoding="utf-8",
        )
        events = read_ground_truth(ground_truth_path)
        delivery_counts = delivery_status_summary(
            events,
            [device["device_code"] for device in configured_devices],
        )
        print(f"Simulator ground truth: {ground_truth_path}")
        print(f"Simulator log: {simulator_log_path}")
        print("Delivery counts by device (including retry/failure HTTP statuses):")
        for device_code, counts in delivery_counts.items():
            print(
                f"  {device_code}: generated={counts['generated']}, "
                f"accepted={counts['accepted']} "
                f"{dict(counts['accepted_by_status'])}, "
                f"omitted={counts['omitted']}, "
                f"retries={dict(counts['retries_by_status'])}, "
                f"failures={dict(counts['failures_by_status'])}"
            )
        self.assertEqual(
            simulation.returncode,
            0,
            f"Simulator failed; see {simulator_log_path}. "
            f"stdout={simulation.stdout}\nstderr={simulation.stderr}",
        )
        failures = [event for event in events if event["event"] == "delivery_failed"]
        self.assertFalse(
            failures,
            "Simulator delivery failures (status counts above): "
            f"{failures[:5]}; ground truth: {ground_truth_path}",
        )
        main_code = configured_devices[0]["device_code"]
        main_received_times = [
            datetime.fromisoformat(event["received_at"])
            for event in events
            if event["event"] == "reading_accepted"
            and event["device_code"] == main_code
            and event.get("received_at")
        ]
        self.assertTrue(
            main_received_times,
            "The main device has no accepted server receive timestamps; "
            f"delivery counts are above and ground truth is at {ground_truth_path}.",
        )
        received_after = min(main_received_times)
        received_before = max(main_received_times)
        scheduled = [
            event for event in events if event["event"] == "fault_scheduled"
        ]
        main_faults = [fault for fault in scheduled if fault["device_code"] == main_code]
        self.assertEqual({fault["type"] for fault in main_faults}, set(FAULT_TYPES))
        for fault in main_faults:
            start_tick = round(
                fault["simulated_start_seconds"] / TICK_SECONDS
            )
            self.assertGreaterEqual(
                start_tick,
                WARMUP_READINGS,
                f"{fault['type']} must begin after warm-up",
            )
        main_faults.sort(key=lambda item: item["simulated_start_seconds"])
        for previous, current in zip(main_faults, main_faults[1:]):
            normal_gap = (
                current["simulated_start_seconds"]
                - previous["simulated_end_seconds"]
            ) / TICK_SECONDS - 1
            self.assertGreaterEqual(
                round(normal_gap),
                40,
                f"Insufficient normal baseline between {previous['type']} and {current['type']}",
            )

        generated_by_key = {
            event["idempotency_key"]: event
            for event in events
            if event["event"] == "reading_generated"
        }
        accepted = [
            event for event in events if event["event"] == "reading_accepted"
        ]
        by_device: dict[str, list[dict[str, Any]]] = {}
        measurement_labels: dict[int, dict[str, Any]] = {}
        for event in accepted:
            by_device.setdefault(event["device_code"], []).append(event)
            generated = generated_by_key[event["idempotency_key"]]
            measurement_labels[int(event["measurement_id"])] = {
                "simulated_time_seconds": generated["simulated_time_seconds"],
                "active_fault_ids": generated["active_fault_ids"],
                "value": generated["value"],
                "device_code": event["device_code"],
            }
        for configured_device in configured_devices:
            device_code = configured_device["device_code"]
            counts = delivery_counts[device_code]
            expected_ticks = int(configured_device["sample_count"])
            expected_omissions = (
                20
                if device_code == configured_devices[0]["device_code"]
                else 0
            )
            expected_generated = expected_ticks - expected_omissions
            self.assertEqual(
                counts["generated"],
                expected_generated,
                f"{device_code} stopped before its configured sample count; "
                f"delivery counts are above.",
            )
            self.assertEqual(counts["omitted"], expected_omissions)
            self.assertEqual(
                counts["generated"],
                counts["accepted"] + sum(
                    1
                    for event in failures
                    if event.get("device_code") == device_code
                ),
                f"{device_code} has generated readings missing from accepted/failure ledger",
            )
            self.assertEqual(
                counts["accepted"],
                expected_generated,
                f"{device_code} accepted count differs from configured samples "
                "after accounting only for the scheduled dropout.",
            )
        main_measurement_positions = {
            int(event["measurement_id"]): position
            for position, event in enumerate(by_device[main_code])
        }
        main_measurement_ids = set(main_measurement_positions)
        auxiliary_measurement_ids = {
            int(event["measurement_id"])
            for device_config in configured_devices[1:]
            for event in by_device[device_config["device_code"]]
        }
        self.assertTrue(main_measurement_ids.isdisjoint(auxiliary_measurement_ids))
        expected_main_readings = (
            int(configured_devices[0]["sample_count"])
            - delivery_counts[main_code]["omitted"]
        )
        self.assertEqual(len(by_device.get(main_code, [])), expected_main_readings)
        for device_config in configured_devices[1:]:
            self.assertEqual(
                len(by_device.get(device_config["device_code"], [])),
                int(device_config["sample_count"]),
                f"Unexpected reading count for {device_config['device_code']}",
            )
        unique_measurement_ids = [
            int(event["measurement_id"]) for event in accepted
        ]
        self.assertEqual(len(unique_measurement_ids), len(set(unique_measurement_ids)))
        for device_code, (device, parameter) in device_rows.items():
            measurements_response = client.request(
                "GET",
                f"/api/v1/devices/{device['id']}/measurements"
                f"?parameter_id={parameter['id']}&limit=500&offset=0",
            )
            accepted_ids = {
                int(event["measurement_id"])
                for event in by_device.get(device_code, [])
            }
            returned = {
                item["id"]: item for item in measurements_response["items"]
            }
            self.assertTrue(accepted_ids.issubset(returned))
            self.assertTrue(
                all(returned[item_id]["source_type"] == "simulated" for item_id in accepted_ids)
            )
            self.assertTrue(
                all(
                    returned[item_id]["parameter_id"] == parameter["id"]
                    and received_after
                    <= datetime.fromisoformat(returned[item_id]["received_at"])
                    <= received_before
                    for item_id in accepted_ids
                ),
                "Run readings must match the selected parameter and receive-time window.",
            )

        point_spike = next(
            fault for fault in main_faults if fault["type"] == "point_spike"
        )
        point_id = point_spike["fault_id"]
        spike_events = [
            event
            for event in accepted
            if event["device_code"] == main_code
            and point_id
            in generated_by_key[event["idempotency_key"]]["active_fault_ids"]
        ]
        self.assertEqual(len(spike_events), 1)
        alert_evaluation = spike_events[0].get("alert_evaluation") or {}
        self.assertEqual(
            alert_evaluation.get("priority"),
            "watch",
            "A single injected point spike must remain a watch, not an elevated alert.",
        )

        stuck_fault = next(
            fault for fault in main_faults if fault["type"] == "stuck_sensor"
        )
        stuck_values = [
            generated_by_key[event["idempotency_key"]]["value"]
            for event in accepted
            if event["device_code"] == main_code
            and stuck_fault["fault_id"]
            in generated_by_key[event["idempotency_key"]]["active_fault_ids"]
        ]
        self.assertGreaterEqual(len(stuck_values), 10)
        self.assertEqual(len(set(stuck_values)), 1)
        dropout_fault = next(
            fault for fault in main_faults if fault["type"] == "signal_dropout"
        )
        omitted = [
            event
            for event in events
            if event["event"] == "reading_omitted"
            and event["device_code"] == main_code
            and dropout_fault["simulated_start_seconds"]
            <= event["simulated_time_seconds"]
            <= dropout_fault["simulated_end_seconds"]
        ]
        self.assertEqual(len(omitted), 20)
        self.assertFalse(
            any(
                dropout_fault["simulated_start_seconds"]
                <= label["simulated_time_seconds"]
                <= dropout_fault["simulated_end_seconds"]
                for label in measurement_labels.values()
                if label["device_code"] == main_code
            ),
            "Dropout slots must be absent, not converted into zero measurements.",
        )
        main_device = device_rows[main_code][0]
        dashboard = client.request("GET", "/api/v1/monitoring/dashboard")
        main_dashboard_device = next(
            item for item in dashboard["devices"] if item["id"] == main_device["id"]
        )
        dropout_schedule = next(
            item
            for item in main_dashboard_device["measurement_schedule"]
            if item["parameter_id"] == device_rows[main_code][1]["id"]
        )
        stale_episode = dropout_schedule["stale_data_episode"]
        self.assertIsNotNone(
            stale_episode,
            "The scheduled stale-data sweep did not persist a dropout episode.",
        )
        self.assertIn("stale_data", stale_episode["alert_rules"])
        self.assertEqual(
            stale_episode["status"],
            "closed",
            "The stale-data episode did not close after readings resumed.",
        )
        self.assertLessEqual(
            received_after,
            datetime.fromisoformat(stale_episode["first_detected_at"]),
        )
        self.assertLessEqual(
            datetime.fromisoformat(stale_episode["first_detected_at"]),
            received_before,
        )
        self.assertIsNotNone(stale_episode["closed_at"])
        stuck_fault = next(
            fault for fault in main_faults if fault["type"] == "stuck_sensor"
        )
        stuck_flatline_alerts = [
            event
            for event in accepted
            if event["device_code"] == main_code
            and stuck_fault["simulated_start_seconds"]
            <= generated_by_key[event["idempotency_key"]]["simulated_time_seconds"]
            <= stuck_fault["simulated_end_seconds"]
            and "flatline" in (
                (event.get("alert_evaluation") or {}).get("alert_rules") or []
            )
        ]
        first_flatline = (
            min(
                stuck_flatline_alerts,
                key=lambda event: generated_by_key[event["idempotency_key"]][
                    "simulated_time_seconds"
                ],
            )
            if stuck_flatline_alerts
            else None
        )
        stuck_reading_events = [
            event
            for event in accepted
            if event["device_code"] == main_code
            and stuck_fault["simulated_start_seconds"]
            <= generated_by_key[event["idempotency_key"]]["simulated_time_seconds"]
            <= stuck_fault["simulated_end_seconds"]
        ]
        stuck_reading_values = [
            generated_by_key[event["idempotency_key"]]["value"]
            for event in stuck_reading_events
        ]
        if first_flatline is None:
            first_flatline_position = None
            readings_after_fault_start = None
            fault_start_reading_position = None
            inside_labeled_interval = None
        else:
            first_flatline_position = main_measurement_positions[
                int(first_flatline["measurement_id"])
            ]
            readings_after_fault_start = sum(
                1
                for event in accepted
                if event["device_code"] == main_code
                and stuck_fault["simulated_start_seconds"]
                <= generated_by_key[event["idempotency_key"]]["simulated_time_seconds"]
                <= generated_by_key[first_flatline["idempotency_key"]][
                    "simulated_time_seconds"
                ]
            )
            fault_start_reading_position = min(
                main_measurement_positions[int(event["measurement_id"])]
                for event in stuck_reading_events
            )
            inside_labeled_interval = True
        flatline_evidence = {
            "rule": "flatline",
            "first_alert_at": (
                first_flatline.get("received_at")
                if first_flatline is not None
                else None
            ),
            "fault_start_reading_position": fault_start_reading_position,
            "first_alert_position": first_flatline_position,
            "readings_after_fault_start": readings_after_fault_start,
            "inside_labeled_interval": inside_labeled_interval,
        }
        dropout_evidence = {
            "rule": "stopped_reporting",
            "opened_at": stale_episode["first_detected_at"],
            "closed_at": stale_episode["closed_at"],
            "status": stale_episode["status"],
        }
        results_by_algorithm: dict[str, dict[int, dict[str, Any]]] = {}
        run_ids: dict[str, int] = {}
        runtimes: dict[str, float] = {}
        test_algorithms = [*ALGORITHMS, "ensemble"]
        for algorithm in test_algorithms:
            body = {
                "device_id": device_rows[main_code][0]["id"],
                "parameter_id": device_rows[main_code][1]["id"],
                "algorithm": algorithm,
                "detector_parameters": detector_parameters(algorithm, seed),
                "source_type": "simulated",
                "training_window": ANALYSIS_WINDOW,
                "refit_every": REFIT_EVERY,
                "max_readings": 500,
                "received_after": received_after.isoformat(),
                "received_before": received_before.isoformat(),
                "idempotency_key": f"{run_id}-{algorithm}-first",
            }
            created = client.request(
                "POST",
                "/api/v1/analyses/equipment-history",
                body,
            )
            analysis = wait_for_analysis(client, int(created["id"]))
            self.assertEqual(analysis["source_type"], "equipment")
            self.assertEqual(
                analysis["metrics"]["configuration"]["data_source_type"],
                "simulated",
            )
            result_map = analysis_results(client, int(created["id"]))
            self.assertEqual(len(result_map), analysis["total_records"])
            self.assertEqual(
                set(result_map),
                main_measurement_ids,
                "Analysis results must contain exactly this run's accepted main-device readings.",
            )
            flagged_count = sum(
                bool(item["is_anomaly"]) for item in result_map.values()
            )
            self.assertEqual(analysis["anomaly_count"], flagged_count)
            self.assertEqual(analysis["algorithm_flagged_results"], flagged_count)
            self.assertEqual(
                sum(analysis["severity_distribution"].values()),
                flagged_count,
            )
            self.assertEqual(
                analysis["unique_affected_measurements"],
                flagged_count,
            )
            results_by_algorithm[algorithm] = result_map
            run_ids[algorithm] = int(created["id"])
            runtimes[algorithm] = float(
                analysis.get("duration_seconds")
                or analysis["metrics"]["execution_seconds"]
            )

            repeated_body = {
                **body,
                "idempotency_key": f"{run_id}-{algorithm}-repeat",
            }
            repeated = client.request(
                "POST",
                "/api/v1/analyses/equipment-history",
                repeated_body,
            )
            repeated_analysis = wait_for_analysis(client, int(repeated["id"]))
            repeated_results = analysis_results(client, int(repeated["id"]))
            self.assertEqual(
                {
                    measurement_id: item["is_anomaly"]
                    for measurement_id, item in result_map.items()
                },
                {
                    measurement_id: item["is_anomaly"]
                    for measurement_id, item in repeated_results.items()
                },
                f"{algorithm} detections changed when the same seeded data was re-analyzed",
            )
            for measurement_id, item in result_map.items():
                if item["anomaly_score"] is None:
                    continue
                position = main_measurement_positions[measurement_id]
                details = item["result_details"]
                result_keys = sorted(item)
                detail_keys = sorted(details) if isinstance(details, dict) else []
                context = (
                    f"{algorithm} device={main_code} position={position} "
                    "GET /api/v1/analyses/{analysis_id}/results: "
                    f"result keys={result_keys}, result_details keys={detail_keys}"
                )
                self.assertIn("history_state", item, context)
                self.assertLess(
                    details["training_end_position"],
                    position,
                    f"{algorithm} used current/future data for measurement {measurement_id}",
                )
                self.assertEqual(details["source_type"], "simulated")
                self.assertIn(
                    item["history_state"],
                    {"ready", "insufficient_history"},
                    context,
                )
            self.assertEqual(
                repeated_analysis["status"],
                "completed",
            )

        comparison_started = time.perf_counter()
        comparison = client.request(
            "POST",
            "/api/v1/analyses/comparisons",
            {"analysis_ids": list(run_ids.values())},
        )
        comparison_seconds = time.perf_counter() - comparison_started
        self.assertEqual(len(comparison["runs"]), len(test_algorithms))
        self.assertEqual(
            len(comparison["agreement_matrix"]),
            len(test_algorithms) ** 2,
        )
        self.assertNotIn("accuracy", comparison)
        self.assertNotIn("precision", comparison)
        self.assertNotIn("recall", comparison)
        self.assertNotIn("f1", comparison)
        saved_comparison = client.request(
            "GET",
            f"/api/v1/analyses/comparisons/{comparison['id']}",
        )
        self.assertEqual(saved_comparison["id"], comparison["id"])
        self.assertEqual(
            saved_comparison["agreement_matrix"],
            comparison["agreement_matrix"],
        )

        detection_table: list[tuple[str, str, int]] = []
        for fault in main_faults:
            for algorithm in test_algorithms:
                count = sum(
                    1
                    for measurement_id, result in results_by_algorithm[algorithm].items()
                    if result["is_anomaly"]
                    and fault["simulated_start_seconds"]
                    <= measurement_labels[measurement_id]["simulated_time_seconds"]
                    <= fault["simulated_end_seconds"] + 5 * TICK_SECONDS
                )
                detection_table.append((fault["type"], algorithm, count))

        for algorithm in ("ewma", "cusum"):
            for fault_type in (
                "gradual_degradation",
                "sudden_level_shift",
                "slow_drift",
            ):
                self.assertGreater(
                    next(
                        count
                        for row_fault, row_algorithm, count in detection_table
                        if row_fault == fault_type and row_algorithm == algorithm
                    ),
                    0,
                    f"{algorithm} did not flag {fault_type} in or shortly after its labeled interval",
                )

        ensemble_results = results_by_algorithm["ensemble"]
        for result in ensemble_results.values():
            details = result["result_details"]
            if details.get("high_confidence"):
                self.assertGreaterEqual(details.get("agreement_count", 0), 2)
                self.assertGreaterEqual(details.get("voted_count", 0), 2)
            self.assertGreaterEqual(details.get("confidence", 0), 0)
            self.assertLessEqual(details.get("confidence", 1), 1)

        print(f"\nGround-truth detections (simulation runtime {simulation_seconds:.2f}s)")
        print(f"{'Fault':<24} {'Detector':<24} {'Flagged':>8}")
        print("-" * 60)
        for fault_type, algorithm, count in detection_table:
            print(f"{fault_type:<24} {algorithm:<24} {count:>8}")
        print("\nDetector/runtime table (seconds)")
        print(f"{'Detector':<24} {'Flagged':>8} {'Runtime':>12}")
        print("-" * 48)
        for algorithm in test_algorithms:
            flagged = sum(
                bool(item["is_anomaly"])
                for item in results_by_algorithm[algorithm].values()
            )
            print(f"{algorithm:<24} {flagged:>8} {runtimes[algorithm]:>12.3f}")
        print(f"{'comparison':<24} {'-':>8} {comparison_seconds:>12.3f}")
        print("\nRule-layer evidence")
        print(f"{'Fault':<24} {'Rule':<22} Evidence")
        print("-" * 110)
        print(
            f"{'stuck_sensor':<24} {flatline_evidence['rule']:<22} "
            f"first_alert_at={flatline_evidence['first_alert_at']}; "
            f"readings_after_fault_start={flatline_evidence['readings_after_fault_start']}; "
            f"inside_interval={flatline_evidence['inside_labeled_interval']}"
        )
        print(
            f"{'signal_dropout':<24} {dropout_evidence['rule']:<22} "
            f"opened_at={dropout_evidence['opened_at']}; "
            f"closed_at={dropout_evidence['closed_at']}; "
            f"status={dropout_evidence['status']}"
        )
        stuck_parameter = device_rows[main_code][1]
        flatline_settings = {
            "consecutive_readings": stuck_parameter["flatline_reading_count"],
            "variance_tolerance": stuck_parameter["flatline_variance_tolerance"],
        }
        stuck_baseline_values = [
            float(
                event["alert_evaluation"]["flatline_evaluation"]["baseline_std"]
            )
            for event in stuck_reading_events
            if event.get("alert_evaluation", {}).get("flatline_evaluation", {}).get(
                "baseline_std"
            )
            is not None
        ]
        near_interval_seconds = 10 * (
            stuck_parameter["expected_interval_seconds"] or 3
        )
        nearby_flatline_alerts = [
            {
                "simulated_time_seconds": generated_by_key[
                    event["idempotency_key"]
                ]["simulated_time_seconds"],
                "alert_rules": (event.get("alert_evaluation") or {}).get(
                    "alert_rules", []
                ),
            }
            for event in accepted
            if event["device_code"] == main_code
            and stuck_fault["simulated_start_seconds"] - near_interval_seconds
            <= generated_by_key[event["idempotency_key"]]["simulated_time_seconds"]
            <= stuck_fault["simulated_end_seconds"] + near_interval_seconds
            and "flatline"
            in ((event.get("alert_evaluation") or {}).get("alert_rules") or [])
        ]
        print("\nStuck-sensor flatline evidence")
        print(
            f"interval=[{stuck_fault['simulated_start_seconds']}, "
            f"{stuck_fault['simulated_end_seconds']}]s; "
            f"readings={len(stuck_reading_events)}; values={stuck_reading_values}"
        )
        print(
            f"flatline_settings={flatline_settings}; "
            f"baseline_std_values={stuck_baseline_values}"
        )
        print(f"nearby_flatline_alerts={nearby_flatline_alerts}")
        if first_flatline is None:
            print(
                "no flatline alert found inside the stuck-sensor interval "
                f"[{stuck_fault['simulated_start_seconds']}, "
                f"{stuck_fault['simulated_end_seconds']}]; "
                f"readings={len(stuck_reading_events)}"
            )
        self.assertTrue(
            stuck_flatline_alerts,
            "The flatline rule did not fire inside the labeled stuck-sensor interval.",
        )
        print(
            "Stuck sensor: the simulator holds the generated signal constant; "
            "whether it is flagged depends on each detector's statistical model. "
            "Dropout: no measurement is ingested, so detectors cannot score the "
            "missing interval; it is represented only by ground-truth omission events."
        )


class FullIntegrationDiagnosticsTests(unittest.TestCase):
    def test_seed_can_be_overridden_by_environment(self) -> None:
        with patch.dict(os.environ, {"SIMULATOR_SEED": "9912"}):
            self.assertEqual(integration_seed(20261028), 9912)
        with patch.dict(os.environ, {"SIMULATOR_SEED": "invalid"}):
            with self.assertRaisesRegex(AssertionError, "must be an integer"):
                integration_seed(20261028)
        with patch.dict(os.environ, {"SIMULATOR_SEED": "-1"}):
            with self.assertRaisesRegex(AssertionError, "non-negative"):
                integration_seed(20261028)

    def test_delivery_status_counts_are_grouped_by_device_and_status(self) -> None:
        summary = delivery_status_summary(
            [
                {"event": "reading_generated", "device_code": "SIM-1"},
                {
                    "event": "reading_accepted",
                    "device_code": "SIM-1",
                    "http_status": 201,
                },
                {
                    "event": "delivery_retry",
                    "device_code": "SIM-1",
                    "status_code": 429,
                    "error_type": "http",
                },
                {
                    "event": "delivery_failed",
                    "device_code": "SIM-2",
                    "status_code": 422,
                    "error_type": "http",
                },
            ],
            ["SIM-1", "SIM-2"],
        )
        self.assertEqual(summary["SIM-1"]["generated"], 1)
        self.assertEqual(summary["SIM-1"]["accepted"], 1)
        self.assertEqual(summary["SIM-1"]["accepted_by_status"]["HTTP 201"], 1)
        self.assertEqual(summary["SIM-1"]["retries_by_status"]["HTTP 429"], 1)
        self.assertEqual(summary["SIM-2"]["failures_by_status"]["HTTP 422"], 1)


if __name__ == "__main__":
    unittest.main()
