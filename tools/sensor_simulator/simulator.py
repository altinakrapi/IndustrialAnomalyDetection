from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


LOGGER = logging.getLogger("sensor_simulator")
MAX_READINGS_PER_MINUTE = 120
MAX_BATCH_SIZE = 100
FAULT_TYPES = {
    "point_spike",
    "gradual_degradation",
    "sudden_level_shift",
    "stuck_sensor",
    "signal_dropout",
    "increased_variance",
    "slow_drift",
}
DEVICE_TYPES = {"motor", "pump", "compressor", "fan"}
PARAMETER_LIMIT_FIELDS = {
    "lower_limit",
    "upper_limit",
    "warning_low",
    "warning_high",
    "critical_low",
    "critical_high",
}
PARAMETER_DEFAULTS: dict[str, dict[str, Any]] = {
    "Temperature": {
        "unit": "°C",
        "lower_limit": -50,
        "upper_limit": 250,
        "warning_high": 85,
        "critical_high": 110,
    },
    "Vibration RMS": {
        "unit": "mm/s",
        "lower_limit": 0,
        "upper_limit": 50,
        "warning_high": 7.1,
        "critical_high": 11.2,
    },
    "Pressure": {
        "unit": "bar",
        "lower_limit": 0,
        "upper_limit": 30,
        "warning_high": 24,
        "critical_high": 27,
    },
    "Current": {
        "unit": "A",
        "lower_limit": 0,
        "upper_limit": 150,
        "warning_high": 130,
        "critical_high": 145,
    },
    "Rotational Speed": {
        "unit": "RPM",
        "lower_limit": 0,
        "upper_limit": 10000,
        "warning_high": 7000,
        "critical_high": 9000,
    },
}
PARAMETER_LOAD_EFFECT = {
    "Temperature": ("add", 12.0),
    "Vibration RMS": ("multiply", 0.35),
    "Pressure": ("multiply", 0.2),
    "Current": ("multiply", 0.3),
    "Rotational Speed": ("multiply", 0.025),
}
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class SimulatorError(Exception):
    """Raised for invalid simulator configuration or API interactions."""


class APIError(SimulatorError):
    def __init__(
        self, status_code: int, detail: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.headers = headers or {}


class APIClient:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        device_key: str | None = None,
        opener: Callable[..., Any] | None = None,
        timeout_seconds: float = 15,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.device_key = device_key
        self.opener = opener or urllib.request.urlopen
        self.timeout_seconds = timeout_seconds
        self.last_status_code: int | None = None

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.device_key:
            headers["X-Device-API-Key"] = self.device_key
        body = (
            json.dumps(payload, allow_nan=False).encode("utf-8")
            if payload is not None
            else None
        )
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                self.last_status_code = getattr(response, "status", None)
                response_body = response.read()
                if not response_body:
                    return None
                return json.loads(response_body.decode("utf-8"))
        except urllib.error.HTTPError as error:
            self.last_status_code = error.code
            response_body = error.read()
            try:
                parsed = json.loads(response_body.decode("utf-8"))
                detail = (
                    parsed.get("detail", str(error))
                    if isinstance(parsed, dict)
                    else str(error)
                )
                if not isinstance(detail, str):
                    detail = json.dumps(detail, ensure_ascii=True)
            except (UnicodeDecodeError, json.JSONDecodeError):
                detail = f"HTTP {error.code}"
            response_headers = {
                key.lower(): value for key, value in (error.headers or {}).items()
            }
            raise APIError(error.code, detail, response_headers) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SimulatorError(f"API request failed: {error}") from error
        except json.JSONDecodeError as error:
            raise SimulatorError("API returned a non-JSON response") from error

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("POST", path, payload)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)


class GroundTruthWriter:
    def __init__(self, path: Path, run_id: str, seed: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.run_id = run_id
        self.seed = seed
        self._file = path.open("x", encoding="utf-8", newline="\n")
        self.write(
            {
                "event": "run_started",
                "run_id": run_id,
                "seed": seed,
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )

    def write(self, entry: dict[str, Any]) -> None:
        record = {"run_id": self.run_id, "seed": self.seed, **entry}
        self._file.write(
            json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        )
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        if not self._file.closed:
            self.write(
                {
                    "event": "run_finished",
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._file.close()

    def __enter__(self) -> GroundTruthWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class PerDeviceRateLimiter:
    def __init__(
        self,
        *,
        limit: int = MAX_READINGS_PER_MINUTE,
        window_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock
        self.sleeper = sleeper
        self._reservations: dict[str, deque[tuple[float, int]]] = defaultdict(deque)

    def reserve(self, device_code: str, count: int) -> None:
        if count < 1 or count > MAX_BATCH_SIZE:
            raise SimulatorError(f"Batch size must be from 1 to {MAX_BATCH_SIZE}")
        if count > self.limit:
            raise SimulatorError("A batch exceeds the per-device rate limit")
        reservations = self._reservations[device_code]
        while True:
            now = self.clock()
            while (
                reservations
                and now - reservations[0][0] >= self.window_seconds
            ):
                reservations.popleft()
            used = sum(amount for _, amount in reservations)
            if used + count <= self.limit:
                reservations.append((now, count))
                return
            wait_seconds = max(
                0.001,
                reservations[0][0] + self.window_seconds - now,
            )
            self.sleeper(wait_seconds)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError as error:
        raise SimulatorError(f"Configuration file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise SimulatorError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(data, dict):
        raise SimulatorError("Configuration must be a JSON object")
    return data


def load_config(path: Path) -> dict[str, Any]:
    config = _read_json(path)
    api_base_url = os.environ.get(
        "SIMULATOR_API_BASE_URL", config.get("api_base_url", "")
    )
    if not isinstance(api_base_url, str):
        raise SimulatorError("api_base_url must be a URL string")
    config["api_base_url"] = api_base_url.rstrip("/")
    if not config["api_base_url"]:
        raise SimulatorError(
            "Set api_base_url in the config or SIMULATOR_API_BASE_URL"
        )
    if not config["api_base_url"].startswith(("http://", "https://")):
        raise SimulatorError("api_base_url must use http:// or https://")
    for key, default in (
        ("send_interval_seconds", 5),
        ("duration_seconds", 300),
        ("speed_up", 1),
        ("seed", 1),
        ("random_faults_per_device", 0),
        ("random_fault_duration_seconds", 600),
        ("max_delivery_attempts", 5),
    ):
        config.setdefault(key, default)
    for key in ("send_interval_seconds", "duration_seconds", "speed_up"):
        value = config[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise SimulatorError(f"{key} must be a finite positive number")
    if (
        isinstance(config["seed"], bool)
        or not isinstance(config["seed"], int)
    ):
        raise SimulatorError("seed must be an integer")
    if (
        isinstance(config["random_faults_per_device"], bool)
        or not isinstance(config["random_faults_per_device"], int)
        or config["random_faults_per_device"] < 0
    ):
        raise SimulatorError("random_faults_per_device must be a non-negative integer")
    random_fault_duration = config["random_fault_duration_seconds"]
    if (
        isinstance(random_fault_duration, bool)
        or not isinstance(random_fault_duration, (int, float))
        or not math.isfinite(random_fault_duration)
        or random_fault_duration <= 0
    ):
        raise SimulatorError(
            "random_fault_duration_seconds must be a finite positive number"
        )
    attempts = config["max_delivery_attempts"]
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 20:
        raise SimulatorError("max_delivery_attempts must be between 1 and 20")
    devices = config.get("devices")
    if not isinstance(devices, list) or not devices:
        raise SimulatorError("Configure at least one device")
    codes: set[str] = set()
    for device in devices:
        if not isinstance(device, dict):
            raise SimulatorError("Each device configuration must be an object")
        for field in ("name", "device_code", "device_type"):
            if not isinstance(device.get(field), str) or not device[field].strip():
                raise SimulatorError(f"Every device requires a non-empty {field}")
        if device["device_type"] not in DEVICE_TYPES:
            raise SimulatorError(
                f"Unsupported device type {device['device_type']!r}; "
                f"choose from {', '.join(sorted(DEVICE_TYPES))}"
            )
        code = device["device_code"]
        if code in codes:
            raise SimulatorError(f"Duplicate configured device code: {code}")
        codes.add(code)
        if device.get("operating_profile", "continuous") not in {
            "continuous",
            "working_hours",
        }:
            raise SimulatorError(f"Unsupported operating profile for {code}")
        parameters = device.get("parameters")
        if not isinstance(parameters, list) or not parameters:
            raise SimulatorError(f"{code} must have at least one parameter")
        if len(parameters) > MAX_BATCH_SIZE:
            raise SimulatorError(f"{code} exceeds the batch size limit")
        if any(not isinstance(parameter, str) for parameter in parameters):
            raise SimulatorError(f"{code} parameter names must be strings")
        if len(set(parameters)) != len(parameters):
            raise SimulatorError(f"{code} contains duplicate parameters")
        for mapping_name in (
            "baselines",
            "noise",
            "wear_per_simulated_hour",
        ):
            mapping = device.get(mapping_name, {})
            if not isinstance(mapping, dict):
                raise SimulatorError(f"{code} {mapping_name} must be an object")
        for parameter in parameters:
            if parameter not in PARAMETER_DEFAULTS:
                raise SimulatorError(
                    f"Unsupported parameter {parameter!r} for {code}"
                )
            baseline = device.get("baselines", {}).get(parameter)
            if (
                isinstance(baseline, bool)
                or not isinstance(baseline, (int, float))
                or not math.isfinite(baseline)
            ):
                raise SimulatorError(
                    f"{code} needs a finite baseline for {parameter}"
                )
            noise = device.get("noise", {}).get(parameter, 0)
            if (
                isinstance(noise, bool)
                or not isinstance(noise, (int, float))
                or not math.isfinite(noise)
                or noise < 0
            ):
                raise SimulatorError(f"Noise for {code}/{parameter} must be non-negative")
            wear = device.get("wear_per_simulated_hour", {}).get(parameter, 0)
            if (
                isinstance(wear, bool)
                or not isinstance(wear, (int, float))
                or not math.isfinite(wear)
            ):
                raise SimulatorError(
                    f"Wear rate for {code}/{parameter} must be finite"
                )
            limits_config = device.get("parameter_limits", {})
            intervals_config = device.get("parameter_intervals_minutes", {})
            intervals_seconds_config = device.get(
                "parameter_intervals_seconds", {}
            )
            if not isinstance(limits_config, dict) or not isinstance(
                intervals_config, dict
            ) or not isinstance(intervals_seconds_config, dict):
                raise SimulatorError(
                    f"{code} parameter limits and interval settings must be objects"
                )
            interval_seconds = intervals_seconds_config.get(parameter)
            if interval_seconds is not None and (
                isinstance(interval_seconds, bool)
                or not isinstance(interval_seconds, int)
                or not 1 <= interval_seconds <= 31_536_000
            ):
                raise SimulatorError(
                    f"{code} interval for {parameter} must be 1..31536000 seconds"
                )
            parameter_limits = limits_config.get(parameter, {})
            if not isinstance(parameter_limits, dict):
                raise SimulatorError(
                    f"Limits for {code}/{parameter} must be an object"
                )
            unknown_limit_fields = set(parameter_limits) - PARAMETER_LIMIT_FIELDS
            if unknown_limit_fields:
                raise SimulatorError(
                    f"Unsupported limit fields for {code}/{parameter}: "
                    f"{', '.join(sorted(unknown_limit_fields))}"
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in parameter_limits.values()
            ):
                raise SimulatorError(
                    f"Limits for {code}/{parameter} must be finite numbers"
                )
            limits = _parameter_payload(parameter, device)
            if limits["lower_limit"] >= limits["upper_limit"]:
                raise SimulatorError(
                    f"Lower limit must be below upper limit for {code}/{parameter}"
                )
            if not limits["lower_limit"] <= baseline <= limits["upper_limit"]:
                raise SimulatorError(
                    f"Baseline for {code}/{parameter} must be within its configured limits"
                )
            interval = intervals_config.get(parameter, 1)
            if (
                isinstance(interval, bool)
                or not isinstance(interval, int)
                or not 1 <= interval <= 525600
            ):
                raise SimulatorError(
                    f"Measurement interval for {code}/{parameter} must be "
                    "an integer from 1 to 525600 minutes"
                )
        sample_count = device.get("sample_count")
        if sample_count is not None and (
            isinstance(sample_count, bool)
            or not isinstance(sample_count, int)
            or not 1 <= sample_count <= 5000
        ):
            raise SimulatorError(
                f"{code} sample_count must be an integer from 1 to 5000"
            )
    faults = config.get("faults", [])
    if not isinstance(faults, list):
        raise SimulatorError("faults must be a JSON array")
    for fault in faults:
        _validate_fault(fault, codes, devices)
    return config


def _validate_fault(
    fault: dict[str, Any], codes: set[str], devices: list[dict[str, Any]]
) -> None:
    if not isinstance(fault, dict) or fault.get("type") not in FAULT_TYPES:
        raise SimulatorError("Each fault must use a supported fault type")
    code = fault.get("device_code")
    if not isinstance(code, str) or code not in codes:
        raise SimulatorError(f"Fault references unknown device {code!r}")
    device = next(item for item in devices if item["device_code"] == code)
    if fault.get("parameter") not in device["parameters"]:
        raise SimulatorError(f"Fault parameter is not configured for {code}")
    start = fault.get("start_seconds")
    end = fault.get("end_seconds")
    magnitude = fault.get("magnitude")
    if (
        not isinstance(start, (int, float))
        or not isinstance(end, (int, float))
        or isinstance(start, bool)
        or isinstance(end, bool)
        or not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end < start
    ):
        raise SimulatorError("Fault start/end seconds must be ordered and non-negative")
    if (
        isinstance(magnitude, bool)
        or not isinstance(magnitude, (int, float))
        or not math.isfinite(magnitude)
        or magnitude <= 0
    ):
        raise SimulatorError("Fault magnitude must be finite and positive")
    severity = fault.get("severity", "medium")
    if not isinstance(severity, str) or severity not in {
        "low",
        "medium",
        "high",
        "critical",
    }:
        raise SimulatorError("Fault severity is not supported")


def _parameter_payload(name: str, config: dict[str, Any]) -> dict[str, Any]:
    limits = dict(PARAMETER_DEFAULTS[name])
    limits.update(config.get("parameter_limits", {}).get(name, {}))
    payload = {
        "name": name,
        **limits,
        "expected_interval_minutes": config.get("parameter_intervals_minutes", {}).get(
            name, 1
        ),
        "expected_interval_seconds": config.get(
            "parameter_intervals_seconds", {}
        ).get(name),
        "analysis_method": "isolation_forest",
        "z_score_threshold": 3.0,
        "isolation_contamination": 0.05,
        "enabled": True,
    }
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, path)


def setup_devices(config: dict[str, Any], state_path: Path) -> dict[str, Any]:
    email = os.environ.get("SIMULATOR_ADMIN_EMAIL")
    password = os.environ.get("SIMULATOR_ADMIN_PASSWORD")
    if not email or not password:
        raise SimulatorError(
            "Set SIMULATOR_ADMIN_EMAIL and SIMULATOR_ADMIN_PASSWORD for setup"
        )
    anonymous_client = APIClient(config["api_base_url"])
    login = anonymous_client.post(
        "/api/v1/auth/login",
        {"email": email, "password": password},
    )
    token = login.get("access_token")
    if not token:
        raise SimulatorError("Login response did not contain an access token")
    admin = APIClient(config["api_base_url"], bearer_token=token)
    current_user = admin.get("/api/v1/auth/me")
    if current_user.get("role") != "administrator":
        raise SimulatorError("Device setup requires an Administrator account")

    existing_devices = admin.get("/api/v1/devices?limit=500&offset=0")
    if not isinstance(existing_devices, list):
        raise SimulatorError("The device list API returned an unexpected response")
    by_code = {device["device_code"]: device for device in existing_devices}
    old_state = _read_json(state_path) if state_path.exists() else {}
    old_device_state = old_state.get("devices", {})
    new_device_state: dict[str, Any] = {}

    for device_config in config["devices"]:
        code = device_config["device_code"]
        device = by_code.get(code)
        if device is None:
            device = admin.post(
                "/api/v1/devices",
                {
                    "name": device_config["name"],
                    "device_code": code,
                    "device_type": device_config["device_type"],
                    "category": "Simulated test equipment",
                    "location": "Simulator",
                    "description": "Synthetic simulator asset; not physical equipment.",
                    "status": "active",
                    "source_type": "simulated",
                },
            )
            by_code[code] = device
        elif device.get("source_type") != "simulated":
            raise SimulatorError(
                f"Device code {code} already belongs to non-simulated equipment; "
                "choose a different code."
            )
        if device.get("device_type") != device_config["device_type"]:
            raise SimulatorError(f"Existing simulated device {code} has a different type")

        device_id = device["id"]
        existing_parameters = admin.get(
            f"/api/v1/devices/{device_id}/parameters"
        )
        parameters_by_name = {
            parameter["name"].casefold(): parameter
            for parameter in existing_parameters
        }
        for parameter_name in device_config["parameters"]:
            parameter_payload = _parameter_payload(parameter_name, device_config)
            existing_parameter = parameters_by_name.get(parameter_name.casefold())
            if existing_parameter is None:
                created = admin.post(
                    f"/api/v1/devices/{device_id}/parameters",
                    parameter_payload,
                )
                parameters_by_name[parameter_name.casefold()] = created
            elif existing_parameter["unit"] != parameter_payload["unit"]:
                raise SimulatorError(
                    f"Existing {code}/{parameter_name} unit is "
                    f"{existing_parameter['unit']!r}, expected "
                    f"{parameter_payload['unit']!r}; setup will not alter it."
                )

        api_keys = admin.get(
            f"/api/v1/device-api-keys/devices/{device_id}"
        )
        previous = old_device_state.get(code, {})
        local_key = previous.get("api_key")
        active_keys = [
            item for item in api_keys if item.get("revoked_at") is None
        ]
        if (
            local_key
            and active_keys
            and any(
                item["key_prefix"] == local_key[:12] for item in active_keys
            )
        ):
            api_key = local_key
        else:
            if active_keys:
                key_response = admin.post(
                    f"/api/v1/device-api-keys/devices/{device_id}/rotate",
                    {},
                )
            else:
                key_response = admin.post(
                    f"/api/v1/device-api-keys/devices/{device_id}",
                    {},
                )
            api_key = key_response.get("api_key")
            if not api_key:
                raise SimulatorError(f"Key creation returned no key for {code}")
        new_device_state[code] = {
            "device_id": device_id,
            "device_code": code,
            "api_key": api_key,
        }
        LOGGER.info("Ready simulated device %s (id=%s)", code, device_id)

    state = {
        "api_base_url": config["api_base_url"],
        "devices": new_device_state,
    }
    _atomic_write_json(state_path, state)
    return state


def _normalize_fault(
    fault: dict[str, Any], fault_number: int, run_id: str
) -> dict[str, Any]:
    return {
        **fault,
        "fault_id": f"{run_id}-f{fault_number:04d}",
        "severity": fault.get("severity", "medium"),
    }


def build_fault_schedule(
    config: dict[str, Any], run_id: str, simulated_duration: float
) -> list[dict[str, Any]]:
    faults = [
        _normalize_fault(fault, index, run_id)
        for index, fault in enumerate(config.get("faults", []), start=1)
    ]
    rng = random.Random(config["seed"])
    next_id = len(faults) + 1
    tick_seconds = (
        float(config["send_interval_seconds"]) * float(config["speed_up"])
    )
    for device in config["devices"]:
        for _ in range(config["random_faults_per_device"]):
            parameters = device["parameters"]
            parameter = rng.choice(parameters)
            start = rng.uniform(
                0,
                max(0, simulated_duration - config["random_fault_duration_seconds"]),
            )
            start = math.floor(start / tick_seconds) * tick_seconds
            fault = {
                "device_code": device["device_code"],
                "parameter": parameter,
                "type": rng.choice(sorted(FAULT_TYPES)),
                "start_seconds": start,
                "end_seconds": start + config["random_fault_duration_seconds"],
                "magnitude": rng.uniform(0.5, 5.0),
                "severity": rng.choice(["low", "medium", "high"]),
            }
            faults.append(_normalize_fault(fault, next_id, run_id))
            next_id += 1
    return faults


def deterministic_run_id(config: dict[str, Any], override: str | None = None) -> str:
    if override:
        if (
            not isinstance(override, str)
            or len(override) > 40
            or not re.fullmatch(r"[A-Za-z0-9_-]+", override)
        ):
            raise SimulatorError("run_id must contain 1-40 safe characters")
        return override.lower()
    content = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        f"{config['seed']}:{content}".encode("utf-8")
    ).hexdigest()[:16]


class SignalGenerator:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.stuck_values: dict[str, float] = {}

    @staticmethod
    def _load(device: dict[str, Any], simulated_seconds: float) -> float:
        hour = (simulated_seconds / 3600) % 24
        daily_cycle = 0.5 + 0.35 * math.sin(2 * math.pi * (hour - 6) / 24)
        if device.get("operating_profile") == "working_hours":
            return min(1.0, max(0.08, daily_cycle))
        return min(1.0, max(0.45, 0.72 + 0.08 * math.sin(2 * math.pi * hour / 24)))

    def normal_value(
        self,
        device: dict[str, Any],
        parameter: str,
        simulated_seconds: float,
        *,
        noise_multiplier: float = 1.0,
    ) -> float:
        baseline = float(device["baselines"][parameter])
        noise = float(device.get("noise", {}).get(parameter, 0))
        wear = float(
            device.get("wear_per_simulated_hour", {}).get(parameter, 0)
        )
        hours = simulated_seconds / 3600
        load = self._load(device, simulated_seconds)
        common_load_noise = self.rng.gauss(0, 0.035)
        load = min(1.0, max(0.0, load + common_load_noise))
        effect, factor = PARAMETER_LOAD_EFFECT[parameter]
        expected = (
            baseline + factor * (load - 0.5)
            if effect == "add"
            else baseline * (1 + factor * (load - 0.5))
        )
        expected *= 1 + wear * hours
        return self.rng.gauss(expected, noise * noise_multiplier)

    def sample(
        self,
        device: dict[str, Any],
        parameter: str,
        simulated_seconds: float,
        faults: list[dict[str, Any]],
    ) -> tuple[float | None, list[str]]:
        active_faults = [
            fault
            for fault in faults
            if fault["device_code"] == device["device_code"]
            and fault["parameter"] == parameter
            and fault["start_seconds"] <= simulated_seconds <= fault["end_seconds"]
        ]
        variance_factor = math.prod(
            max(1.0, float(fault["magnitude"]))
            for fault in active_faults
            if fault["type"] == "increased_variance"
        )
        value = self.normal_value(
            device,
            parameter,
            simulated_seconds,
            noise_multiplier=variance_factor,
        )
        for fault in active_faults:
            fault_type = fault["type"]
            magnitude = float(fault["magnitude"])
            progress = (
                1.0
                if fault["end_seconds"] == fault["start_seconds"]
                else (simulated_seconds - fault["start_seconds"])
                / (fault["end_seconds"] - fault["start_seconds"])
            )
            if fault_type == "point_spike":
                value += magnitude
            elif fault_type == "gradual_degradation":
                value *= 1 + magnitude * progress
            elif fault_type == "sudden_level_shift":
                value += magnitude
            elif fault_type == "stuck_sensor":
                key = fault["fault_id"]
                value = self.stuck_values.setdefault(key, value)
            elif fault_type == "signal_dropout":
                return None, [item["fault_id"] for item in active_faults]
            elif fault_type == "slow_drift":
                value += magnitude * progress
        return value, [fault["fault_id"] for fault in active_faults]


def _retry_delay(error: APIError, attempt: int) -> float:
    if error.status_code == 429:
        retry_after = error.headers.get("retry-after")
        if retry_after:
            try:
                return max(0, min(120, float(retry_after)))
            except ValueError:
                pass
    return min(30, 2**attempt)


def deliver_batch(
    client: APIClient,
    *,
    device_code: str,
    readings: list[dict[str, Any]],
    max_attempts: int,
    rate_limiter: PerDeviceRateLimiter | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    on_retry: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    if not readings:
        return None
    payload = {"device_identifier": device_code, "readings": readings}
    for attempt in range(max_attempts):
        try:
            return client.post("/api/v1/readings/batch", payload)
        except APIError as error:
            retryable = error.status_code in RETRYABLE_STATUSES
            if not retryable or attempt + 1 >= max_attempts:
                LOGGER.error(
                    "Delivery rejected for %s (HTTP %s): %s",
                    device_code,
                    error.status_code,
                    error,
                )
                raise
            delay = _retry_delay(error, attempt)
            LOGGER.warning(
                "Temporary delivery failure for %s (HTTP %s); retrying in %.1fs",
                device_code,
                error.status_code,
                delay,
            )
            if on_retry is not None:
                on_retry(
                    {
                        "event": "delivery_retry",
                        "device_code": device_code,
                        "attempt": attempt + 1,
                        "error_type": "http",
                        "status_code": error.status_code,
                        "error": str(error),
                        "retry_delay_seconds": delay,
                    }
                )
        except SimulatorError as error:
            if attempt + 1 >= max_attempts:
                LOGGER.error("Delivery failed for %s: %s", device_code, error)
                raise
            delay = min(30, 2**attempt)
            LOGGER.warning(
                "Temporary delivery failure for %s; retrying in %.1fs: %s",
                device_code,
                delay,
                error,
            )
            if on_retry is not None:
                on_retry(
                    {
                        "event": "delivery_retry",
                        "device_code": device_code,
                        "attempt": attempt + 1,
                        "error_type": "network",
                        "status_code": None,
                        "error": str(error),
                        "retry_delay_seconds": delay,
                    }
                )
        sleeper(delay)
    return None


def _accepted_timestamp(reading: dict[str, Any]) -> str | None:
    return reading.get("received_at") or reading.get("measured_at")


def run_simulation(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    ground_truth_path: Path,
    duration_seconds: float | None = None,
    speed_up: float | None = None,
    seed: int | None = None,
    run_id_override: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    rate_limiter: PerDeviceRateLimiter | None = None,
) -> Path:
    duration = (
        float(config["duration_seconds"])
        if duration_seconds is None
        else duration_seconds
    )
    time_scale = float(config["speed_up"]) if speed_up is None else speed_up
    effective_seed = config["seed"] if seed is None else seed
    if (
        not math.isfinite(duration)
        or not math.isfinite(time_scale)
        or duration <= 0
        or time_scale <= 0
    ):
        raise SimulatorError("Duration and speed-up factor must be positive")
    effective_config = {**config, "seed": effective_seed}
    run_id = deterministic_run_id(effective_config, run_id_override)
    sample_count_mode = all(
        device.get("sample_count") is not None
        for device in config["devices"]
    )
    sample_count_horizon = max(
        (
            int(device["sample_count"]) - 1
            for device in config["devices"]
            if device.get("sample_count") is not None
        ),
        default=0,
    ) * float(config["send_interval_seconds"]) * time_scale
    simulated_duration = max(duration * time_scale, sample_count_horizon)
    faults = build_fault_schedule(effective_config, run_id, simulated_duration)
    device_state = state.get("devices", {})
    for device in config["devices"]:
        entry = device_state.get(device["device_code"])
        if not entry or not entry.get("api_key"):
            raise SimulatorError(
                f"No provisioned API key found for {device['device_code']}; run setup first"
            )
    sender_limit = rate_limiter or PerDeviceRateLimiter(sleeper=sleeper, clock=clock)
    generator = SignalGenerator(effective_seed)
    clients = {
        device["device_code"]: APIClient(
            state.get("api_base_url", config["api_base_url"]),
            device_key=device_state[device["device_code"]]["api_key"],
        )
        for device in config["devices"]
    }
    sequence = 0
    device_ticks = {device["device_code"]: 0 for device in config["devices"]}
    start = clock()
    final_sample = 0.0
    tick_index = 0
    with GroundTruthWriter(ground_truth_path, run_id, effective_seed) as ledger:
        for index, fault in enumerate(faults, start=1):
            ledger.write(
                {
                    "event": "fault_scheduled",
                    "fault_id": fault["fault_id"],
                    "fault_index": index,
                    "device_code": fault["device_code"],
                    "parameter": fault["parameter"],
                    "type": fault["type"],
                    "simulated_start_seconds": fault["start_seconds"],
                    "simulated_end_seconds": fault["end_seconds"],
                    "severity": fault["severity"],
                    "magnitude": fault["magnitude"],
                }
            )
        try:
            while (
                (
                    sample_count_mode
                    and any(
                        device_ticks[device["device_code"]]
                        < int(device["sample_count"])
                        for device in config["devices"]
                    )
                )
                or clock() - start < duration
                or tick_index == 0
            ):
                simulated_seconds = (
                    tick_index
                    * float(config["send_interval_seconds"])
                    * time_scale
                )
                final_sample = simulated_seconds
                if simulated_seconds > simulated_duration:
                    break
                tick_index += 1
                for device in config["devices"]:
                    device_code = device["device_code"]
                    sample_count = device.get("sample_count")
                    if (
                        sample_count is not None
                        and device_ticks[device_code] >= sample_count
                    ):
                        continue
                    device_ticks[device_code] += 1
                    readings: list[dict[str, Any]] = []
                    generated_metadata: list[dict[str, Any]] = []
                    for parameter in device["parameters"]:
                        value, active_fault_ids = generator.sample(
                            device,
                            parameter,
                            simulated_seconds,
                            faults,
                        )
                        if value is None:
                            ledger.write(
                                {
                                    "event": "reading_omitted",
                                    "device_code": device_code,
                                    "parameter": parameter,
                                    "simulated_time_seconds": simulated_seconds,
                                    "reason": "signal_dropout",
                                    "active_fault_ids": active_fault_ids,
                                }
                            )
                            continue
                        sequence += 1
                        idempotency_key = (
                            f"sim-{run_id[:16]}-{sequence:08d}"
                        )
                        reading = {
                            "parameter": parameter,
                            "value": round(value, 8),
                            "unit": PARAMETER_DEFAULTS[parameter]["unit"],
                            "idempotency_key": idempotency_key,
                        }
                        ledger.write(
                            {
                                "event": "reading_generated",
                                "device_code": device_code,
                                "parameter": parameter,
                                "value": reading["value"],
                                "unit": reading["unit"],
                                "simulated_time_seconds": simulated_seconds,
                                "active_fault_ids": active_fault_ids,
                                "idempotency_key": idempotency_key,
                            }
                        )
                        readings.append(reading)
                        generated_metadata.append(
                            {
                                "parameter": parameter,
                                "idempotency_key": idempotency_key,
                            }
                        )
                    try:
                        result = deliver_batch(
                            clients[device_code],
                            device_code=device_code,
                            readings=readings,
                            max_attempts=config["max_delivery_attempts"],
                            sleeper=sleeper,
                            on_retry=ledger.write,
                        )
                    except SimulatorError as error:
                        for item in generated_metadata:
                            api_error = error if isinstance(error, APIError) else None
                            ledger.write(
                                {
                                    "event": "delivery_failed",
                                    "device_code": device_code,
                                    **item,
                                    "error_type": (
                                        "http" if api_error is not None else "network"
                                    ),
                                    "status_code": (
                                        api_error.status_code
                                        if api_error is not None
                                        else None
                                    ),
                                    "error": str(error),
                                }
                            )
                        continue
                    if result is None:
                        continue
                    accepted_by_key = {
                        item.get("idempotency_key"): item
                        for item in result.get("readings", [])
                    }
                    accepted_by_parameter = {
                        item.get("parameter_name"): item
                        for item in result.get("readings", [])
                    }
                    for item in generated_metadata:
                        response = accepted_by_key.get(
                            item["idempotency_key"]
                        ) or accepted_by_parameter.get(item["parameter"])
                        if response is None:
                            ledger.write(
                                {
                                    "event": "delivery_failed",
                                    "device_code": device_code,
                                    **item,
                                    "error_type": "response",
                                    "status_code": None,
                                    "error": "API success response omitted this reading",
                                }
                            )
                            continue
                        ledger.write(
                            {
                                "event": "reading_accepted",
                                "device_code": device_code,
                                **item,
                                "measurement_id": response.get("id"),
                                "measured_at": _accepted_timestamp(response),
                                "received_at": response.get("received_at"),
                                "http_status": getattr(
                                    clients[device_code], "last_status_code", None
                                ),
                                "duplicate": response.get("duplicate", False),
                                "analysis_status": response.get("analysis_status"),
                                "anomaly_count": response.get("anomaly_count"),
                                "alert_evaluation": response.get("alert_evaluation"),
                            }
                        )
                if all(
                    device.get("sample_count") is not None
                    and device_ticks[device["device_code"]]
                    >= device["sample_count"]
                    for device in config["devices"]
                ):
                    break
                LOGGER.info(
                    "Simulation sample at %.1f simulated seconds complete",
                    simulated_seconds,
                )
                if not sample_count_mode and clock() - start >= duration:
                    break
                sleeper(float(config["send_interval_seconds"]))
        except KeyboardInterrupt:
            LOGGER.info("Interrupt received; finalizing ground-truth ledger")
        ledger.write(
            {
                "event": "simulation_finished",
                "simulated_time_seconds": final_sample,
            }
        )
    LOGGER.info("Ground truth saved to %s", ground_truth_path)
    return ground_truth_path


def _default_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parent
    return root / "config.json", root / "devices.local.json"


def build_parser() -> argparse.ArgumentParser:
    config_default, state_default = _default_paths()
    parser = argparse.ArgumentParser(
        description="Simulate explicitly labeled industrial sensor readings via the public API."
    )
    parser.add_argument(
        "--config", type=Path, default=config_default, help="JSON simulator config"
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=state_default,
        help="Git-ignored local device-key state file",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("setup", help="Create simulated devices, parameters, and API keys")
    run_parser = subparsers.add_parser("run", help="Send simulated readings")
    run_parser.add_argument("--duration", type=float, help="Wall-clock run duration in seconds")
    run_parser.add_argument("--speed-up", type=float, help="Simulated seconds per wall second")
    run_parser.add_argument("--seed", type=int, help="Override the configured random seed")
    run_parser.add_argument(
        "--run-id",
        help="Unique run identifier (defaults to deterministic seed/config hash)",
    )
    run_parser.add_argument(
        "--ground-truth",
        type=Path,
        help="Output JSONL path (must not already exist)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("SIMULATOR_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "setup":
            setup_devices(config, args.state)
            LOGGER.info("Device setup complete. Secret keys are stored locally in %s", args.state)
            return 0
        state = _read_json(args.state)
        run_id = deterministic_run_id(
            {**config, "seed": args.seed if args.seed is not None else config["seed"]},
            args.run_id,
        )
        ground_truth = args.ground_truth or (
            args.config.resolve().parent / "runs" / f"ground_truth-{run_id}.jsonl"
        )
        run_simulation(
            config,
            state,
            ground_truth_path=ground_truth,
            duration_seconds=args.duration,
            speed_up=args.speed_up,
            seed=args.seed,
            run_id_override=args.run_id,
        )
        return 0
    except (SimulatorError, OSError, ValueError) as error:
        LOGGER.error("%s", error)
        return 2
    except KeyboardInterrupt:
        LOGGER.info("Stopped by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
