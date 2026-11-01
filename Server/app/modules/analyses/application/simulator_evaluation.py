from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.analyses.application.evaluation_metrics import (
    aggregate_metric_runs,
    binary_metrics,
    label_readings,
)
from app.modules.analyses.domain.detectors import (
    DETECTOR_REGISTRY,
    normalize_algorithm_name,
)
from app.shared.models import (
    AlertEpisode,
    AnalysisResult,
    AnalysisRun,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    SimulatorEvaluation,
    SimulatorFaultInterval,
    SimulatorGroundTruthReading,
    SimulatorGroundTruthRun,
)

MAX_GROUND_TRUTH_BYTES = 20 * 1024 * 1024
MAX_GROUND_TRUTH_LINES = 20_000
MAX_EVALUATION_RUNS = 10
MAX_ANALYSES_PER_RUN = 32


class GroundTruthValidationError(ValueError):
    pass


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise GroundTruthValidationError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise GroundTruthValidationError(f"{field} is not a valid ISO timestamp") from error
    if parsed.tzinfo is None:
        raise GroundTruthValidationError(f"{field} must include a timezone")
    return parsed


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GroundTruthValidationError(f"{field} must be a non-negative number")
    if not math.isfinite(value) or value < 0:
        raise GroundTruthValidationError(f"{field} must be a non-negative number")
    return float(value)


def import_simulator_ground_truth(
    db: Session,
    contents: bytes,
    *,
    purpose: str,
    imported_by: int,
) -> SimulatorGroundTruthRun:
    if purpose not in {"evaluation", "development"}:
        raise GroundTruthValidationError("purpose must be evaluation or development")
    if not contents or len(contents) > MAX_GROUND_TRUTH_BYTES:
        raise GroundTruthValidationError(
            f"Ground-truth file must be between 1 byte and {MAX_GROUND_TRUTH_BYTES} bytes"
        )
    events: list[dict[str, Any]] = []
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise GroundTruthValidationError("Ground-truth file must be UTF-8 JSONL") from error
    lines = text.splitlines()
    if len(lines) > MAX_GROUND_TRUTH_LINES:
        raise GroundTruthValidationError(
            f"Ground-truth file exceeds {MAX_GROUND_TRUTH_LINES} JSONL events"
        )
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise GroundTruthValidationError(
                f"Invalid JSON on line {line_number}"
            ) from error
        if not isinstance(event, dict):
            raise GroundTruthValidationError(
                f"JSONL event on line {line_number} must be an object"
            )
        events.append(event)
    if not events:
        raise GroundTruthValidationError("Ground-truth file contains no events")

    start_events = [event for event in events if event.get("event") == "run_started"]
    finish_events = [event for event in events if event.get("event") == "run_finished"]
    if len(start_events) != 1 or len(finish_events) != 1:
        raise GroundTruthValidationError(
            "Ground-truth file must contain one run_started and one run_finished event"
        )
    run_id = start_events[0].get("run_id")
    seed = start_events[0].get("seed")
    if not isinstance(run_id, str) or not run_id or len(run_id) > 100:
        raise GroundTruthValidationError("run_started must contain a valid run_id")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise GroundTruthValidationError("run_started must contain an integer seed")
    for event in events:
        event_run_id = event.get("run_id")
        event_seed = event.get("seed")
        if event_run_id is not None and event_run_id != run_id:
            raise GroundTruthValidationError("JSONL events contain multiple run IDs")
        if event_seed is not None and event_seed != seed:
            raise GroundTruthValidationError("JSONL events contain multiple seeds")
    if db.scalar(
        select(SimulatorGroundTruthRun.id).where(
            SimulatorGroundTruthRun.run_id == run_id
        )
    ) is not None:
        raise GroundTruthValidationError(f"Run {run_id} has already been imported")

    scheduled: dict[str, dict[str, Any]] = {}
    generated: dict[str, dict[str, Any]] = {}
    accepted: list[dict[str, Any]] = []
    for event in events:
        kind = event.get("event")
        if kind == "fault_scheduled":
            fault_id = event.get("fault_id")
            fault_type = event.get("type")
            device_code = event.get("device_code")
            parameter_name = event.get("parameter")
            if not all(
                isinstance(value, str) and value
                for value in (fault_id, fault_type, device_code, parameter_name)
            ):
                raise GroundTruthValidationError(
                    "fault_scheduled events require fault_id, type, device_code and parameter"
                )
            if (
                len(fault_id) > 120
                or len(fault_type) > 100
                or len(device_code) > 100
                or len(parameter_name) > 100
            ):
                raise GroundTruthValidationError(
                    "fault_scheduled field lengths exceed the supported limits"
                )
            if fault_id in scheduled:
                raise GroundTruthValidationError(f"Duplicate fault_id: {fault_id}")
            begin = _positive_number(
                event.get("simulated_start_seconds"), "simulated_start_seconds"
            )
            end = _positive_number(
                event.get("simulated_end_seconds"), "simulated_end_seconds"
            )
            if end < begin:
                raise GroundTruthValidationError(
                    f"Fault {fault_id} ends before it starts"
                )
            scheduled[fault_id] = {
                "fault_id": fault_id,
                "fault_type": fault_type,
                "device_code": device_code,
                "parameter_name": parameter_name,
                "start_seconds": begin,
                "end_seconds": end,
                "severity": str(event.get("severity", "unspecified")),
            }
        elif kind == "reading_generated":
            key = event.get("idempotency_key")
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 100
                or key in generated
            ):
                raise GroundTruthValidationError(
                    "reading_generated events require unique idempotency keys"
                )
            generated[key] = event
        elif kind == "reading_accepted":
            if event.get("duplicate") is True or event.get("http_status") != 200:
                raise GroundTruthValidationError(
                    "Ground-truth run contains a duplicate or unsuccessful delivery"
                )
            accepted.append(event)

    if not scheduled:
        raise GroundTruthValidationError("Ground-truth run has no labeled fault intervals")
    if not accepted:
        raise GroundTruthValidationError("Ground-truth run has no accepted readings")
    measurement_ids = [event.get("measurement_id") for event in accepted]
    idempotency_keys = [event.get("idempotency_key") for event in accepted]
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in measurement_ids
    ):
        raise GroundTruthValidationError(
            "Accepted readings must contain positive measurement IDs"
        )
    if (
        any(not isinstance(key, str) or not key or len(key) > 100 for key in idempotency_keys)
        or len(set(measurement_ids)) != len(measurement_ids)
        or len(set(idempotency_keys)) != len(idempotency_keys)
    ):
        raise GroundTruthValidationError("Accepted reading events contain duplicates")

    devices = {
        device.device_code: device
        for device in db.scalars(
            select(Device).where(
                Device.device_code.in_(
                    {
                        fault["device_code"] for fault in scheduled.values()
                    }.union(
                        {
                            event.get("device_code")
                            for event in accepted
                            if isinstance(event.get("device_code"), str)
                        }
                    )
                )
            )
        )
    }
    if not devices:
        raise GroundTruthValidationError("No matching simulator devices were found")
    if any(device.source_type != "simulated" for device in devices.values()):
        raise GroundTruthValidationError(
            "Ground-truth imports may only reference simulated devices"
        )

    parameter_by_key: dict[tuple[int, str], EquipmentParameter] = {}
    parameter_rows = db.scalars(
        select(EquipmentParameter).where(
            EquipmentParameter.device_id.in_([device.id for device in devices.values()])
        )
    ).all()
    parameter_by_key = {
        (parameter.device_id, parameter.name): parameter
        for parameter in parameter_rows
    }
    faults: list[dict[str, Any]] = []
    for fault in scheduled.values():
        device = devices.get(fault["device_code"])
        parameter = (
            parameter_by_key.get((device.id, fault["parameter_name"]))
            if device
            else None
        )
        if device is None or parameter is None:
            raise GroundTruthValidationError(
                f"No simulated device parameter for {fault['device_code']} / "
                f"{fault['parameter_name']}"
            )
        faults.append({**fault, "device": device, "parameter": parameter})

    generated_by_key = generated
    measurement_rows = db.execute(
        select(EquipmentMeasurement, EquipmentParameter, Device)
        .join(
            EquipmentParameter,
            EquipmentMeasurement.parameter_id == EquipmentParameter.id,
        )
        .join(Device, EquipmentParameter.device_id == Device.id)
        .where(EquipmentMeasurement.id.in_(measurement_ids))
    ).all()
    measurements = {
        measurement.id: (measurement, parameter, device)
        for measurement, parameter, device in measurement_rows
    }
    if set(measurements) != set(measurement_ids):
        missing = sorted(set(measurement_ids) - set(measurements))
        raise GroundTruthValidationError(
            f"Ground-truth measurement IDs are not present in the database: {missing[:10]}"
        )

    reading_rows: list[dict[str, Any]] = []
    for event in accepted:
        key = event.get("idempotency_key")
        generated_event = generated_by_key.get(key)
        if generated_event is None:
            raise GroundTruthValidationError(
                f"Accepted reading {key} has no matching reading_generated event"
            )
        measurement, parameter, device = measurements[event["measurement_id"]]
        if (
            measurement.source_type != "simulated"
            or device.source_type != "simulated"
            or event.get("device_code") != device.device_code
            or generated_event.get("device_code") != device.device_code
            or event.get("parameter") != parameter.name
            or generated_event.get("parameter") != parameter.name
        ):
            raise GroundTruthValidationError(
                f"Reading {key} does not match a simulated database measurement"
            )
        received_at = _parse_timestamp(event.get("received_at"), "received_at")
        stored_received_at = measurement.received_at
        if stored_received_at.tzinfo is None:
            stored_received_at = stored_received_at.replace(tzinfo=received_at.tzinfo)
        if abs((stored_received_at - received_at).total_seconds()) > 0.001:
            raise GroundTruthValidationError(
                f"Reading {key} received_at does not match the stored measurement"
            )
        active_fault_ids = generated_event.get("active_fault_ids", [])
        if not isinstance(active_fault_ids, list) or any(
            not isinstance(fault_id, str) or fault_id not in scheduled
            for fault_id in active_fault_ids
        ):
            raise GroundTruthValidationError(
                f"Reading {key} contains an unknown active fault ID"
            )
        active_types = [
            scheduled[fault_id]["fault_type"] for fault_id in active_fault_ids
        ]
        if any(
            scheduled[fault_id]["device_code"] != device.device_code
            or scheduled[fault_id]["parameter_name"] != parameter.name
            for fault_id in active_fault_ids
        ):
            raise GroundTruthValidationError(
                f"Reading {key} has a fault label for another device or parameter"
            )
        reading_rows.append(
            {
                "measurement": measurement,
                "parameter": parameter,
                "device": device,
                "simulated_time_seconds": _positive_number(
                    generated_event.get("simulated_time_seconds"),
                    "simulated_time_seconds",
                ),
                "received_at": received_at,
                "fault_types": active_types,
            }
        )

    received_values = [reading["received_at"] for reading in reading_rows]
    run = SimulatorGroundTruthRun(
        run_id=run_id,
        seed=seed,
        purpose=purpose,
        source_sha256=hashlib.sha256(contents).hexdigest(),
        configuration={
            "event_count": len(events),
            "fault_count": len(faults),
            "source_type": "simulated",
            "format": "sensor_simulator_jsonl",
        },
        device_ids=sorted({reading["device"].id for reading in reading_rows}),
        received_after=min(received_values),
        received_before=max(received_values),
        accepted_reading_count=len(reading_rows),
        imported_by=imported_by,
    )
    db.add(run)
    db.flush()
    db.add_all(
        [
            SimulatorFaultInterval(
                ground_truth_run_id=run.id,
                fault_id=fault["fault_id"],
                device_id=fault["device"].id,
                parameter_id=fault["parameter"].id,
                device_code=fault["device_code"],
                parameter_name=fault["parameter_name"],
                fault_type=fault["fault_type"],
                start_seconds=fault["start_seconds"],
                end_seconds=fault["end_seconds"],
                severity=fault["severity"],
            )
            for fault in faults
        ]
    )
    db.add_all(
        [
            SimulatorGroundTruthReading(
                ground_truth_run_id=run.id,
                measurement_id=reading["measurement"].id,
                device_id=reading["device"].id,
                parameter_id=reading["parameter"].id,
                device_code=reading["device"].device_code,
                parameter_name=reading["parameter"].name,
                simulated_time_seconds=reading["simulated_time_seconds"],
                received_at=reading["received_at"],
                fault_types=reading["fault_types"],
            )
            for reading in reading_rows
        ]
    )
    db.flush()
    return run


def load_development_ground_truth_runs(
    db: Session,
    ground_truth_run_ids: list[int],
) -> list[SimulatorGroundTruthRun]:
    if not ground_truth_run_ids:
        raise ValueError("At least one development ground-truth run is required")
    if len(set(ground_truth_run_ids)) != len(ground_truth_run_ids):
        raise ValueError("Ground-truth run IDs must be unique")
    runs = list(
        db.scalars(
            select(SimulatorGroundTruthRun).where(
                SimulatorGroundTruthRun.id.in_(ground_truth_run_ids)
            )
        )
    )
    if len(runs) != len(ground_truth_run_ids):
        raise LookupError("One or more simulator ground-truth runs were not found")
    if any(run.purpose != "development" for run in runs):
        raise ValueError(
            "Detector tuning may use only ground-truth runs with purpose='development'"
        )
    return runs


def _timestamp_for_simulated_time(
    readings: list[SimulatorGroundTruthReading], simulated_time: float
) -> datetime | None:
    ordered = sorted(readings, key=lambda reading: reading.simulated_time_seconds)
    if not ordered:
        return None
    for left, right in zip(ordered, ordered[1:]):
        if left.simulated_time_seconds <= simulated_time <= right.simulated_time_seconds:
            span = right.simulated_time_seconds - left.simulated_time_seconds
            if span <= 0:
                return left.received_at
            fraction = (simulated_time - left.simulated_time_seconds) / span
            return left.received_at + (
                right.received_at - left.received_at
            ) * fraction
    closest = min(
        ordered, key=lambda reading: abs(reading.simulated_time_seconds - simulated_time)
    )
    return closest.received_at


def _point_metrics(
    rows: list[SimulatorGroundTruthReading],
    intervals: list[SimulatorFaultInterval],
    results_by_measurement: dict[int, AnalysisResult],
    tolerance_readings: int,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[int, int], list[SimulatorGroundTruthReading]] = defaultdict(list)
    for row in rows:
        grouped[(row.device_id, row.parameter_id)].append(row)
    scored: list[dict[str, Any]] = []
    for key, group in grouped.items():
        group.sort(key=lambda row: row.simulated_time_seconds)
        group_intervals = [
            interval
            for interval in intervals
            if (interval.device_id, interval.parameter_id) == key
            and interval.fault_type != "signal_dropout"
        ]
        time_values = [row.simulated_time_seconds for row in group]
        serialized_intervals = [
            {
                "type": interval.fault_type,
                "start_seconds": interval.start_seconds,
                "end_seconds": interval.end_seconds,
            }
            for interval in group_intervals
        ]
        labels, type_labels = label_readings(
            time_values, serialized_intervals, tolerance_readings
        )
        for row, is_anomaly, row_fault_types in zip(
            group, labels, type_labels, strict=True
        ):
            result = results_by_measurement.get(row.measurement_id)
            if result is None or result.anomaly_score is None:
                continue
            scored.append(
                {
                    "reading": row,
                    "result": result,
                    "label": is_anomaly,
                    "fault_types": row_fault_types,
                }
            )

    overall_data = [item for item in scored if item["label"] or not item["fault_types"]]
    overall = binary_metrics(
        [item["label"] for item in overall_data],
        [item["result"].is_anomaly for item in overall_data],
        [item["result"].anomaly_score for item in overall_data],
    )
    fault_metrics: dict[str, dict[str, Any]] = {}
    for fault_type in sorted(
        {interval.fault_type for interval in intervals if interval.fault_type != "signal_dropout"}
    ):
        # Build this class's positives and globally-normal negatives; overlapping
        # fault windows are excluded rather than mislabeled as normal.
        selected: list[dict[str, Any]] = []
        for item in scored:
            if fault_type in item["fault_types"] and set(item["fault_types"]) == {
                fault_type
            }:
                selected.append({**item, "fault_label": True})
            elif not item["fault_types"]:
                selected.append({**item, "fault_label": False})
        fault_metrics[fault_type] = binary_metrics(
            [item["fault_label"] for item in selected],
            [item["result"].is_anomaly for item in selected],
            [item["result"].anomaly_score for item in selected],
        )
    return overall, fault_metrics, scored


def _mean_and_spread(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "spread_stddev": None}
    mean = sum(values) / len(values)
    return {
        "mean": mean,
        "spread_stddev": math.sqrt(
            sum((value - mean) ** 2 for value in values) / len(values)
        ),
    }


def _fault_delays(
    scored: list[dict[str, Any]],
    rows: list[SimulatorGroundTruthReading],
    intervals: list[SimulatorFaultInterval],
    tolerance_readings: int,
) -> list[dict[str, Any]]:
    delays: list[dict[str, Any]] = []
    grouped: dict[tuple[int, int], list[SimulatorGroundTruthReading]] = defaultdict(list)
    scored_by_group: dict[
        tuple[int, int], dict[int, dict[str, Any]]
    ] = defaultdict(dict)
    for row in rows:
        grouped[(row.device_id, row.parameter_id)].append(row)
    for item in scored:
        key = (item["reading"].device_id, item["reading"].parameter_id)
        scored_by_group[key][item["reading"].measurement_id] = item
    for interval in intervals:
        if interval.fault_type == "signal_dropout":
            continue
        all_rows = grouped[(interval.device_id, interval.parameter_id)]
        all_rows.sort(key=lambda row: row.simulated_time_seconds)
        positions = [
            index
            for index, row in enumerate(all_rows)
            if interval.start_seconds <= row.simulated_time_seconds <= interval.end_seconds
        ]
        start_position = (
            positions[0]
            if positions
            else next(
                (
                    index
                    for index, row in enumerate(all_rows)
                    if row.simulated_time_seconds >= interval.start_seconds
                ),
                None,
            )
        )
        if start_position is None:
            delays.append(
                {
                    "fault_id": interval.fault_id,
                    "fault_type": interval.fault_type,
                    "detected": False,
                    "delay_readings": None,
                    "delay_seconds": None,
                }
            )
            continue
        scored_by_id = scored_by_group[(interval.device_id, interval.parameter_id)]
        last_positive_position = min(
            len(all_rows) - 1,
            (positions[-1] if positions else start_position) + tolerance_readings,
        )
        detected_item = next(
            (
                scored_by_id[row.measurement_id]
                for row in all_rows[start_position : last_positive_position + 1]
                if row.measurement_id in scored_by_id
                and scored_by_id[row.measurement_id]["result"].is_anomaly
            ),
            None,
        )
        delay_readings = None
        delay_seconds = None
        if detected_item is not None:
            detected_position = next(
                index
                for index, row in enumerate(all_rows)
                if row.measurement_id == detected_item["reading"].measurement_id
            )
            delay_readings = detected_position - start_position
            delay_seconds = max(
                0.0,
                detected_item["reading"].simulated_time_seconds
                - interval.start_seconds,
            )
        delays.append(
            {
                "fault_id": interval.fault_id,
                "fault_type": interval.fault_type,
                "device_code": interval.device_code,
                "detected": detected_item is not None,
                "delay_readings": delay_readings,
                "delay_seconds": delay_seconds,
            }
        )
    return delays


def _rule_evidence(
    rows: list[SimulatorGroundTruthReading],
    intervals: list[SimulatorFaultInterval],
    episodes: list[AlertEpisode],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[tuple[int, int], list[SimulatorGroundTruthReading]] = defaultdict(list)
    for row in rows:
        grouped[(row.device_id, row.parameter_id)].append(row)
    flatline: list[dict[str, Any]] = []
    dropout: list[dict[str, Any]] = []
    for interval in intervals:
        group = sorted(
            grouped[(interval.device_id, interval.parameter_id)],
            key=lambda row: row.simulated_time_seconds,
        )
        matching_episodes = [
            episode
            for episode in episodes
            if episode.device_id == interval.device_id
            and episode.parameter_id == interval.parameter_id
        ]
        if interval.fault_type == "stuck_sensor":
            start_time = _timestamp_for_simulated_time(group, interval.start_seconds)
            end_time = _timestamp_for_simulated_time(group, interval.end_seconds)
            matched = next(
                (
                    episode
                    for episode in matching_episodes
                    if "flatline" in (episode.alert_rules or [])
                    and start_time
                    and end_time
                    and start_time <= episode.first_detected_at <= end_time
                ),
                None,
            )
            flatline.append(
                {
                    "fault_id": interval.fault_id,
                    "fault_type": interval.fault_type,
                    "device_code": interval.device_code,
                    "detected": matched is not None,
                    "alert_episode_id": matched.id if matched else None,
                    "opened_at": matched.first_detected_at.isoformat() if matched else None,
                    "status": matched.status if matched else None,
                    "readings_after_fault_start": (
                        sum(
                            start_time <= row.received_at
                            < matched.first_detected_at
                            for row in group
                        )
                        if matched
                        else None
                    ),
                    "rule": "flatline",
                }
            )
        elif interval.fault_type == "signal_dropout":
            before = [
                row
                for row in group
                if row.simulated_time_seconds < interval.start_seconds
            ]
            after = [
                row
                for row in group
                if row.simulated_time_seconds > interval.end_seconds
            ]
            last_before = before[-1] if before else None
            first_after = after[0] if after else None
            matched = next(
                (
                    episode
                    for episode in matching_episodes
                    if "stale_data" in (episode.alert_rules or [])
                    and last_before
                    and episode.first_detected_at >= last_before.received_at
                    and (
                        first_after is None
                        or episode.first_detected_at <= first_after.received_at
                    )
                ),
                None,
            )
            dropout.append(
                {
                    "fault_id": interval.fault_id,
                    "fault_type": interval.fault_type,
                    "device_code": interval.device_code,
                    "detected": matched is not None,
                    "alert_episode_id": matched.id if matched else None,
                    "opened_at": matched.first_detected_at.isoformat() if matched else None,
                    "closed_at": (
                        matched.closed_at.isoformat()
                        if matched and matched.closed_at
                        else None
                    ),
                    "status": matched.status if matched else None,
                    "delay_seconds": (
                        max(
                            0.0,
                            (
                                matched.first_detected_at - last_before.received_at
                            ).total_seconds(),
                        )
                        if matched and last_before
                        else None
                    ),
                    "rule": "stale_data",
                }
            )
    return {"flatline": flatline, "stopped_reporting": dropout}


def create_simulator_evaluation(
    db: Session,
    *,
    runs: list[dict[str, Any]],
    tolerance_readings: int,
    created_by: int,
) -> SimulatorEvaluation:
    if not 0 <= tolerance_readings <= 100:
        raise ValueError("tolerance_readings must be between 0 and 100")
    if not 1 <= len(runs) <= MAX_EVALUATION_RUNS:
        raise ValueError(f"Select between one and {MAX_EVALUATION_RUNS} ground-truth runs")
    gt_ids = [item["ground_truth_run_id"] for item in runs]
    if len(set(gt_ids)) != len(gt_ids):
        raise ValueError("Ground-truth run IDs must be unique")
    if any(not 1 <= len(item["analysis_run_ids"]) <= MAX_ANALYSES_PER_RUN for item in runs):
        raise ValueError(
            f"Each ground-truth run requires between one and {MAX_ANALYSES_PER_RUN} analysis runs"
        )
    all_analysis_ids = [
        analysis_id
        for item in runs
        for analysis_id in item["analysis_run_ids"]
    ]
    if len(set(all_analysis_ids)) != len(all_analysis_ids):
        raise ValueError("Analysis run IDs must not be repeated")
    ground_truth_runs = list(
        db.scalars(
            select(SimulatorGroundTruthRun).where(
                SimulatorGroundTruthRun.id.in_(gt_ids)
            )
        )
    )
    if len(ground_truth_runs) != len(gt_ids):
        raise LookupError("One or more simulator ground-truth runs were not found")
    purpose_set = {run.purpose for run in ground_truth_runs}
    if len(purpose_set) != 1:
        raise ValueError("A multi-run evaluation must use runs with the same purpose")
    limitations = []
    distinct_seed_count = len({run.seed for run in ground_truth_runs})
    if distinct_seed_count < 3:
        limitations.append(
            f"Only {distinct_seed_count} distinct seed(s) are included; fewer than "
            "3 independent seeds is insufficient for stable aggregate estimates."
        )
    if any(run.accepted_reading_count < 1000 for run in ground_truth_runs):
        limitations.append(
            "At least one run has fewer than 1,000 accepted readings; results from "
            "small simulator samples (including approximately 500 readings) are "
            "not reliable performance estimates."
        )
    if purpose_set == {"evaluation"}:
        limitations.append(
            "Evaluation-purpose runs must not be used to select detector settings."
        )
    limitations.append(
        "Metrics apply only to the scored simulated readings and imported fault "
        "labels; results are statistical evidence for human review, not a diagnosis."
    )
    limitations.append(
        "Dropout is evaluated only through stopped-reporting episodes; stuck-sensor "
        "flatline alerts are reported separately from statistical detector scores."
    )
    analyses = list(
        db.scalars(select(AnalysisRun).where(AnalysisRun.id.in_(all_analysis_ids)))
    )
    if len(analyses) != len(all_analysis_ids):
        raise LookupError("One or more analysis runs were not found")
    analyses_by_id = {analysis.id: analysis for analysis in analyses}
    results = list(
        db.scalars(
            select(AnalysisResult).where(
                AnalysisResult.analysis_run_id.in_(all_analysis_ids)
            )
        )
    )
    results_by_run: dict[int, list[AnalysisResult]] = defaultdict(list)
    for result in results:
        results_by_run[result.analysis_run_id].append(result)
    gt_by_id = {run.id: run for run in ground_truth_runs}
    evaluated_runs: list[dict[str, Any]] = []
    detector_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    configurations: list[dict[str, Any]] = []
    truth_run_ids: list[int] = []
    for mapping in runs:
        gt_run = gt_by_id[mapping["ground_truth_run_id"]]
        gt_readings = list(
            db.scalars(
                select(SimulatorGroundTruthReading)
                .where(SimulatorGroundTruthReading.ground_truth_run_id == gt_run.id)
                .order_by(
                    SimulatorGroundTruthReading.device_id,
                    SimulatorGroundTruthReading.parameter_id,
                    SimulatorGroundTruthReading.simulated_time_seconds,
                )
            )
        )
        intervals = list(
            db.scalars(
                select(SimulatorFaultInterval).where(
                    SimulatorFaultInterval.ground_truth_run_id == gt_run.id
                )
            )
        )
        if not gt_readings or not intervals:
            raise ValueError(f"Ground-truth run {gt_run.run_id} has no usable labels")
        measurement_ids = {row.measurement_id for row in gt_readings}
        analysis_by_algorithm: dict[str, list[AnalysisRun]] = defaultdict(list)
        result_by_algorithm: dict[str, dict[int, AnalysisResult]] = defaultdict(dict)
        config_by_algorithm: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for analysis_id in mapping["analysis_run_ids"]:
            analysis = analyses_by_id[analysis_id]
            configuration = (analysis.metrics or {}).get("configuration", {})
            if (
                analysis.status != "completed"
                or analysis.source_type != "equipment"
                or configuration.get("data_source_type") != "simulated"
            ):
                raise ValueError(
                    f"Analysis {analysis.id} must be a completed simulated equipment run"
                )
            algorithm = normalize_algorithm_name(str(configuration.get("algorithm", "")))
            if algorithm not in DETECTOR_REGISTRY:
                raise ValueError(
                    f"Analysis {analysis.id} references an unsupported detector"
                )
            if analysis.device_id not in set(gt_run.device_ids):
                raise ValueError(
                    f"Analysis {analysis.id} does not belong to ground-truth run {gt_run.run_id}"
                )
            analysis_results = results_by_run.get(analysis_id, [])
            matched_results = [
                result
                for result in analysis_results
                if result.equipment_measurement_id in measurement_ids
            ]
            if not matched_results:
                raise ValueError(
                    f"Analysis {analysis.id} has no results linked to run {gt_run.run_id}"
                )
            foreign_results = [
                result
                for result in analysis_results
                if result.equipment_measurement_id is not None
                and result.equipment_measurement_id not in measurement_ids
            ]
            if foreign_results:
                raise ValueError(
                    f"Analysis {analysis.id} contains measurements outside this ground-truth run"
                )
            analysis_by_algorithm[algorithm].append(analysis)
            config_by_algorithm[algorithm].append(
                {
                    "analysis_run_id": analysis.id,
                    "algorithm": algorithm,
                    "parameters": configuration.get("detector_parameters")
                    or configuration.get("parameters")
                    or {},
                    "features": configuration.get("features", []),
                }
            )
            for result in matched_results:
                if result.equipment_measurement_id in result_by_algorithm[algorithm]:
                    raise ValueError(
                        f"Multiple {algorithm} analyses score the same measurement in run {gt_run.run_id}"
                    )
                result_by_algorithm[algorithm][result.equipment_measurement_id] = result
        episodes = list(
            db.scalars(
                select(AlertEpisode).where(
                    AlertEpisode.device_id.in_(gt_run.device_ids),
                    AlertEpisode.created_at
                    >= gt_run.received_after - timedelta(days=1),
                    AlertEpisode.created_at
                    <= gt_run.received_before + timedelta(days=1),
                )
            )
        )
        raw_rule_evidence = _rule_evidence(gt_readings, intervals, episodes)
        run_rule_evidence = {
            rule_name: [
                {
                    **evidence,
                    "ground_truth_run_id": gt_run.id,
                    "run_id": gt_run.run_id,
                    "seed": gt_run.seed,
                    "purpose": gt_run.purpose,
                    "source_type": "simulated",
                }
                for evidence in evidence_items
            ]
            for rule_name, evidence_items in raw_rule_evidence.items()
        }
        algorithms_result: dict[str, Any] = {}
        for algorithm, algorithm_analyses in analysis_by_algorithm.items():
            relevant_device_ids = {analysis.device_id for analysis in algorithm_analyses}
            relevant_parameter_ids = {
                (analysis.metrics or {}).get("configuration", {}).get("parameter_id")
                for analysis in algorithm_analyses
            }
            relevant_parameter_ids.discard(None)
            algorithm_rows = [
                row
                for row in gt_readings
                if row.device_id in relevant_device_ids
                and (
                    not relevant_parameter_ids
                    or row.parameter_id in relevant_parameter_ids
                )
            ]
            algorithm_intervals = [
                interval
                for interval in intervals
                if interval.device_id in relevant_device_ids
                and (
                    not relevant_parameter_ids
                    or interval.parameter_id in relevant_parameter_ids
                )
            ]
            overall, per_fault, scored = _point_metrics(
                algorithm_rows,
                algorithm_intervals,
                result_by_algorithm[algorithm],
                tolerance_readings,
            )
            delays = _fault_delays(
                scored, algorithm_rows, algorithm_intervals, tolerance_readings
            )
            detector_cls = DETECTOR_REGISTRY[algorithm]
            execution_modes = {
                "walk_forward"
                if detector_cls.uses_walk_forward
                and any(
                    result.result_details.get("execution_mode") == "walk_forward"
                    for analysis in algorithm_analyses
                    for result in results_by_run[analysis.id]
                )
                else "batch"
            }
            per_run_result = {
                "ground_truth_run_id": gt_run.id,
                "run_id": gt_run.run_id,
                "seed": gt_run.seed,
                "purpose": gt_run.purpose,
                "source_type": "simulated",
                "algorithm": algorithm,
                "mode": next(iter(execution_modes)),
                "analysis_run_ids": [analysis.id for analysis in algorithm_analyses],
                "detector_settings": config_by_algorithm[algorithm],
                "tolerance_readings": tolerance_readings,
                "scored_reading_count": len(scored),
                "unscored_reading_count": len(algorithm_rows) - len(scored),
                "warmup_handling": (
                    "Only readings with a persisted anomaly score are evaluated; "
                    "unscored warm-up readings are excluded."
                ),
                "limitations": limitations,
                "metrics": overall,
                "per_fault_type": per_fault,
                "detection_delays": delays,
            }
            algorithms_result[algorithm] = per_run_result
            detector_runs[algorithm].append(per_run_result)
            configurations.extend(config_by_algorithm[algorithm])
        evaluated_runs.append(
            {
                "ground_truth_run_id": gt_run.id,
                "run_id": gt_run.run_id,
                "seed": gt_run.seed,
                "purpose": gt_run.purpose,
                "source_type": "simulated",
                "accepted_reading_count": len(gt_readings),
                "fault_intervals": len(intervals),
                "detectors": algorithms_result,
                "rule_evidence": run_rule_evidence,
            }
        )
        truth_run_ids.append(gt_run.id)

    detector_summary: dict[str, Any] = {}
    for algorithm, run_results in detector_runs.items():
        aggregate = aggregate_metric_runs(
            [item["metrics"] for item in run_results]
        )
        fault_types = sorted(
            {
                fault_type
                for item in run_results
                for fault_type in item["per_fault_type"]
            }
        )
        per_fault_aggregate = {
            fault_type: aggregate_metric_runs(
                [
                    item["per_fault_type"][fault_type]
                    for item in run_results
                    if fault_type in item["per_fault_type"]
                ]
            )
            for fault_type in fault_types
        }
        delays_by_fault: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in run_results:
            for delay in item["detection_delays"]:
                delays_by_fault[delay["fault_type"]].append(delay)
        delay_aggregate = {}
        for fault_type, delays in delays_by_fault.items():
            detected = [item for item in delays if item["detected"]]
            detection_rate_values = [
                1.0 if item["detected"] else 0.0 for item in delays
            ]
            reading_delay = _mean_and_spread(
                [
                    float(item["delay_readings"])
                    for item in detected
                    if item["delay_readings"] is not None
                ]
            )
            time_delay = _mean_and_spread(
                [
                    float(item["delay_seconds"])
                    for item in detected
                    if item["delay_seconds"] is not None
                ]
            )
            delay_aggregate[fault_type] = {
                "run_count": len(delays),
                "detected_count": len(detected),
                "detection_rate": len(detected) / len(delays) if delays else None,
                "detection_rate_spread_stddev": _mean_and_spread(
                    detection_rate_values
                )["spread_stddev"],
                "delay_readings": reading_delay,
                "delay_seconds": time_delay,
            }
        detector_summary[algorithm] = {
            "mode_by_run": [
                {
                    "run_id": item["run_id"],
                    "mode": item["mode"],
                }
                for item in run_results
            ],
            "runs": run_results,
            "aggregate": aggregate,
            "per_fault_type_aggregate": per_fault_aggregate,
            "detection_delay_aggregate": delay_aggregate,
        }
    rule_evidence_aggregate: dict[str, dict[str, Any]] = {}
    for rule_name in ("flatline", "stopped_reporting"):
        evidence_items = [
            evidence
            for run in evaluated_runs
            for evidence in run["rule_evidence"][rule_name]
        ]
        detected_items = [item for item in evidence_items if item["detected"]]
        rule_evidence_aggregate[rule_name] = {
            "episode_count": len(evidence_items),
            "run_count": len(
                {
                    item["ground_truth_run_id"]
                    for item in evidence_items
                }
            ),
            "detected_count": len(detected_items),
            "detection_rate": (
                len(detected_items) / len(evidence_items) if evidence_items else None
            ),
            "detection_rate_spread_stddev": _mean_and_spread(
                [1.0 if item["detected"] else 0.0 for item in evidence_items]
            )["spread_stddev"],
            "delay_seconds": _mean_and_spread(
                [
                    float(item["delay_seconds"])
                    for item in detected_items
                    if item.get("delay_seconds") is not None
                ]
            ),
        }
    result_payload = {
        "source_type": "simulated",
        "purpose": next(iter(purpose_set)),
        "tolerance_readings": tolerance_readings,
        "runs": evaluated_runs,
        "detectors": detector_summary,
        "rule_evidence": {
            "runs": [
                {
                    "ground_truth_run_id": run["ground_truth_run_id"],
                    "run_id": run["run_id"],
                    "seed": run["seed"],
                    "purpose": run["purpose"],
                    "source_type": run["source_type"],
                    "flatline": run["rule_evidence"]["flatline"],
                    "stopped_reporting": run["rule_evidence"]["stopped_reporting"],
                }
                for run in evaluated_runs
            ],
            "aggregate": rule_evidence_aggregate,
        },
        "limitations": limitations,
    }
    evaluation = SimulatorEvaluation(
        created_by=created_by,
        purpose=next(iter(purpose_set)),
        configuration={
            "ground_truth_run_ids": truth_run_ids,
            "analysis_run_ids": all_analysis_ids,
            "tolerance_readings": tolerance_readings,
            "detector_settings": configurations,
        },
        ground_truth_run_ids=truth_run_ids,
        analysis_run_ids=all_analysis_ids,
        limitations=" ".join(limitations),
        result=result_payload,
    )
    db.add(evaluation)
    db.flush()
    return evaluation


def evaluation_to_csv(evaluation: SimulatorEvaluation) -> str:
    import csv
    import io

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    metric_names = (
        "precision",
        "recall",
        "f1",
        "roc_auc",
        "pr_auc",
        "false_alarm_rate",
    )
    writer.writerow(
        [
            "run_id",
            "seed",
            "purpose",
            "source_type",
            "detector",
            "mode",
            "fault_type",
            "sample_count",
            "true_positive",
            "false_positive",
            "false_negative",
            "true_negative",
            "precision",
            "recall",
            "f1",
            "roc_auc",
            "pr_auc",
            "false_alarm_rate",
            "tolerance_readings",
            "limitations",
            *[f"{name}_spread_stddev" for name in metric_names],
            "rule_evidence",
        ]
    )

    def write_metrics(
        run_id: str,
        seed: int | str,
        purpose: str,
        algorithm: str,
        mode: str,
        fault_type: str,
        metrics: dict[str, Any],
        tolerance_readings: int,
        spread: dict[str, Any] | None = None,
    ) -> None:
        confusion = metrics.get("confusion_matrix") or {}
        writer.writerow(
            [
                run_id,
                seed,
                purpose,
                "simulated",
                algorithm,
                mode,
                fault_type,
                metrics.get("sample_count"),
                confusion.get("true_positive"),
                confusion.get("false_positive"),
                confusion.get("false_negative"),
                confusion.get("true_negative"),
                metrics.get("precision"),
                metrics.get("recall"),
                metrics.get("f1"),
                metrics.get("roc_auc"),
                metrics.get("pr_auc"),
                metrics.get("false_alarm_rate"),
                tolerance_readings,
                evaluation.limitations,
                *[
                    (spread or {}).get(name, {}).get("spread_stddev")
                    for name in metric_names
                ],
                "",
            ]
        )

    def write_rule(
        run_id: str,
        seed: int | str,
        purpose: str,
        algorithm: str,
        mode: str,
        fault_type: str,
        evidence: dict[str, Any],
        tolerance_readings: int,
    ) -> None:
        writer.writerow(
            [
                run_id,
                seed,
                purpose,
                "simulated",
                algorithm,
                mode,
                fault_type,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                tolerance_readings,
                evaluation.limitations,
                "",
                "",
                "",
                "",
                "",
                "",
                json.dumps(evidence, sort_keys=True),
            ]
        )

    for run in evaluation.result["runs"]:
        for algorithm, detector in run["detectors"].items():
            metric_sets = [("all_faults", detector["metrics"])]
            metric_sets.extend(detector["per_fault_type"].items())
            for fault_type, metrics in metric_sets:
                write_metrics(
                    run["run_id"],
                    run["seed"],
                    run["purpose"],
                    algorithm,
                    detector["mode"],
                    fault_type,
                    metrics,
                    detector["tolerance_readings"],
                )
    for algorithm, detector in evaluation.result["detectors"].items():
        aggregate_groups = [("all_faults", detector["aggregate"])]
        aggregate_groups.extend(detector["per_fault_type_aggregate"].items())
        for fault_type, spread in aggregate_groups:
            means = {
                name: values["mean"] for name, values in spread.items()
            }
            write_metrics(
                "aggregate",
                "",
                evaluation.purpose,
                algorithm,
                "aggregate",
                fault_type,
                means,
                evaluation.configuration["tolerance_readings"],
                spread,
            )
    rule_results = evaluation.result.get("rule_evidence", {})
    for run in rule_results.get("runs", []):
        for rule_name in ("flatline", "stopped_reporting"):
            for evidence in run.get(rule_name, []):
                write_rule(
                    run["run_id"],
                    run["seed"],
                    run["purpose"],
                    "rule_layer",
                    "rule",
                    evidence["fault_type"],
                    evidence,
                    evaluation.configuration["tolerance_readings"],
                )
    for rule_name, evidence in rule_results.get("aggregate", {}).items():
        write_rule(
            "aggregate",
            "",
            evaluation.purpose,
            "rule_layer",
            "rule_aggregate",
            rule_name,
            evidence,
            evaluation.configuration["tolerance_readings"],
        )
    return output.getvalue()
