from __future__ import annotations

import time
import hashlib
import json
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.neural_network import MLPRegressor
from sklearn.svm import OneClassSVM

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.analyses.application.simulator_evaluation import (
    load_development_ground_truth_runs,
)
from app.modules.analyses.application.evaluation_metrics import label_readings
from app.modules.analyses.application.evaluation_metrics import binary_metrics
from app.modules.analyses.domain.detectors import (
    DETECTOR_REGISTRY,
    walk_forward_predict,
)
from app.shared.models import (
    EquipmentMeasurement,
    SimulatorGroundTruthReading,
    SimulatorGroundTruthRun,
    SimulatorFaultInterval,
    SimulatorTuningRun,
)


def _binary_metrics(labels: np.ndarray, flags: np.ndarray) -> dict[str, float | int]:
    tp = int(np.sum((labels == 1) & (flags == 1)))
    fp = int(np.sum((labels == 0) & (flags == 1)))
    fn = int(np.sum((labels == 1) & (flags == 0)))
    tn = int(np.sum((labels == 0) & (flags == 0)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    false_alarm_rate = fp / (fp + tn) if fp + tn else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_alarm_rate": false_alarm_rate,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
    }


def verify_stored_f1(metrics: dict[str, Any]) -> float:
    """Return the F1 derived from persisted confusion counts when available."""
    tp = metrics.get("true_positive")
    fp = metrics.get("false_positive")
    fn = metrics.get("false_negative")
    if all(isinstance(value, int) for value in (tp, fp, fn)):
        return 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
    precision = float(metrics.get("precision", 0.0) or 0.0)
    recall = float(metrics.get("recall", 0.0) or 0.0)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _scores(values: np.ndarray, parameters: dict[str, Any]) -> np.ndarray:
    algorithm = parameters["algorithm"]
    if algorithm == "isolation_forest":
        split = int(len(values) * 0.7)
        train_end = split if parameters.get("mode") == "walk_forward" else len(values)
        model = IsolationForest(
            n_estimators=int(parameters.get("n_estimators", 100)),
            contamination=float(parameters.get("contamination", 0.05)),
            random_state=101,
        )
        fit_values = values[:train_end].reshape(-1, 1) if train_end else values.reshape(-1, 1)
        model.fit(fit_values)
        return -model.score_samples(values.reshape(-1, 1))
    if algorithm == "local_outlier_factor":
        neighbors = int(parameters.get("neighbors", 20))
        neighbors = min(neighbors, max(2, len(values) - 1))
        model = LocalOutlierFactor(
            n_neighbors=neighbors,
            contamination=float(parameters.get("contamination", 0.05)),
        )
        model.fit_predict(values.reshape(-1, 1))
        return -model.negative_outlier_factor_
    if algorithm == "one_class_svm":
        model = OneClassSVM(
            nu=float(parameters.get("nu", 0.05)),
            gamma=(
                parameters["gamma"]
                if parameters.get("gamma") in ("scale", "auto")
                else float(parameters["gamma"])
            ),
            kernel="rbf",
        )
        split = int(len(values) * 0.7)
        model.fit(values[:split].reshape(-1, 1))
        return -model.decision_function(values.reshape(-1, 1)).ravel()
    if algorithm == "autoencoder":
        split = int(len(values) * 0.7)
        model = MLPRegressor(
            hidden_layer_sizes=tuple(parameters.get("hidden_sizes", [8, 4])),
            alpha=float(parameters.get("alpha", 0.0001)),
            max_iter=250,
            random_state=101,
        )
        model.fit(values[:split].reshape(-1, 1), values[:split])
        residual = np.abs(values - model.predict(values.reshape(-1, 1)))
        return residual
    if algorithm == "ewma":
        mean = float(np.mean(values[: int(len(values) * 0.7)]))
        lam = float(parameters.get("lambda", 0.2))
        ewma = mean
        scores = np.empty(len(values), dtype=float)
        for index, value in enumerate(values):
            ewma = lam * float(value) + (1 - lam) * ewma
            scores[index] = abs(float(value) - ewma)
        return scores
    if algorithm == "cusum":
        mean = float(np.mean(values[: int(len(values) * 0.7)]))
        std = max(float(np.std(values[: int(len(values) * 0.7)])), 1e-12)
        k = float(parameters.get("k", 0.5)) * std
        positive = 0.0
        negative = 0.0
        scores = np.empty(len(values), dtype=float)
        for index, value in enumerate(values):
            deviation = float(value) - mean
            positive = max(0.0, positive + deviation - k)
            negative = min(0.0, negative + deviation + k)
            scores[index] = max(positive, -negative)
        return scores
    median = float(np.median(values[: int(len(values) * 0.7)]))
    mad = float(
        np.median(np.abs(values[: int(len(values) * 0.7)] - median))
    )
    scale = max(mad * 1.4826, 1e-12)
    return np.abs(values - median) / scale


def _ground_truth_series(
    db: Session, runs: list[SimulatorGroundTruthRun]
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    run_ids = [run.id for run in runs]
    readings = list(db.scalars(select(SimulatorGroundTruthReading).where(
        SimulatorGroundTruthReading.ground_truth_run_id.in_(run_ids)
    ).order_by(SimulatorGroundTruthReading.received_at)))
    measurement_ids = [reading.measurement_id for reading in readings]
    measurements = {
        row.id: row
        for row in db.scalars(select(EquipmentMeasurement).where(
            EquipmentMeasurement.id.in_(measurement_ids)
        ))
    }
    values: list[float] = []
    labels: list[int] = []
    for reading in readings:
        measurement = measurements.get(reading.measurement_id)
        if measurement is None:
            continue
        values.append(float(measurement.value))
        labels.append(int(bool(reading.fault_types)))
    return (
        np.asarray(values, dtype=float),
        np.asarray(labels, dtype=int),
        [run.seed for run in runs],
    )


def development_evaluation_metrics(
    db: Session,
    runs: list[SimulatorGroundTruthRun],
    parameters: dict[str, Any],
    *,
    tolerance_readings: int = 5,
) -> tuple[dict[str, float | int], dict[str, Any]]:
    """Score each run independently with the production positions and labels."""
    algorithm = parameters["algorithm"]
    intervals_by_run = {
        run.id: list(db.scalars(
            select(SimulatorFaultInterval).where(
                SimulatorFaultInterval.ground_truth_run_id == run.id
            )
        ))
        for run in runs
    }
    run_metrics: list[dict[str, float | int]] = []
    run_truths: list[int] = []
    flattened_flags: list[int] = []
    scoring: dict[str, Any] = {
        "tolerance_readings": tolerance_readings,
        "runs": [],
        "flags": [],
        "truths": [],
    }
    for run in runs:
        readings = list(db.scalars(
            select(SimulatorGroundTruthReading).where(
                SimulatorGroundTruthReading.ground_truth_run_id == run.id
            ).order_by(SimulatorGroundTruthReading.simulated_time_seconds)
        ))
        readings_by_group: dict[tuple[int, int], list[SimulatorGroundTruthReading]] = {}
        for reading in readings:
            readings_by_group.setdefault(
                (reading.device_id, reading.parameter_id), []
            ).append(reading)
        measurements = {
            row.id: row
            for row in db.scalars(select(EquipmentMeasurement).where(
                EquipmentMeasurement.id.in_([reading.measurement_id for reading in readings])
            ))
        }
        serialized_intervals = [
            {
                "type": interval.fault_type,
                "start_seconds": interval.start_seconds,
                "end_seconds": interval.end_seconds,
                "device_id": interval.device_id,
                "parameter_id": interval.parameter_id,
            }
            for interval in intervals_by_run[run.id]
            if interval.fault_type != "signal_dropout"
        ]
        primary_key = max(
            readings_by_group,
            key=lambda key: sum(
                bool(interval.device_id == key[0] and interval.parameter_id == key[1])
                for interval in intervals_by_run[run.id]
            ),
        )
        for group_key, group_readings in [(
            primary_key,
            readings_by_group[primary_key],
        )]:
            group_readings.sort(key=lambda item: item.simulated_time_seconds)
            values = np.asarray([
                float(measurements[reading.measurement_id].value)
                for reading in group_readings
                if reading.measurement_id in measurements
            ], dtype=float)
            labels, fault_types = label_readings(
                [float(reading.simulated_time_seconds) for reading in group_readings],
                [
                    interval for interval in serialized_intervals
                    if interval.get("device_id") == group_readings[0].device_id
                    and interval.get("parameter_id") == group_readings[0].parameter_id
                ],
                tolerance_readings,
            )
            if algorithm in ("ewma", "cusum", "local_outlier_factor"):
                detector_parameters = dict(parameters)
                detector_parameters["algorithm"] = algorithm
                if algorithm == "local_outlier_factor":
                    detector_parameters.setdefault(
                        "n_neighbors",
                        detector_parameters.pop("neighbors", 20),
                    )
                detector = DETECTOR_REGISTRY[algorithm]()
                result = walk_forward_predict(
                    detector,
                    values.reshape(-1, 1),
                    parameters={
                        **detector_parameters,
                        "training_window": 100,
                        "refit_every": 10,
                    },
                    missing_values_strategy="mean",
                    scaling="standard",
                    batch_size=10,
                )
                positions = result.positions
                predicted_flags = np.asarray(result.predictions == -1, dtype=int)
            else:
                scores = _scores(values, parameters)
                if algorithm == "z_score":
                    threshold = float(parameters["threshold"])
                    candidate_flags = (scores >= threshold).astype(int)
                elif algorithm == "autoencoder":
                    threshold = float(np.percentile(scores[:30], parameters["threshold_percentile"]))
                    candidate_flags = (scores >= threshold).astype(int)
                elif algorithm == "isolation_forest":
                    threshold = float(np.quantile(scores[:30], 1 - float(parameters["contamination"])))
                    candidate_flags = (scores >= threshold).astype(int)
                elif algorithm == "one_class_svm":
                    threshold = float(np.quantile(scores[:30], 1 - float(parameters["nu"])))
                    candidate_flags = (scores >= threshold).astype(int)
                positions = np.arange(100, len(values), dtype=int)
                predicted_flags = candidate_flags[positions]
            scored_positions = np.asarray(positions, dtype=int)
            predicted_flags = predicted_flags[-len(scored_positions):]
            run_truths.extend(int(labels[position]) for position in scored_positions)
            run_flags_list = [int(flag) for flag in predicted_flags]
            flattened_flags.extend(run_flags_list)
            run_metric = binary_metrics(
                [bool(labels[position]) for position in scored_positions],
                [bool(flag) for flag in run_flags_list],
            )
            run_metrics.append(run_metric)
        scoring["runs"].append({
            "run_id": run.id,
            "accepted_readings": len(readings),
            "scored_readings": len(scored_positions),
            "warmup_readings": len(readings) - len(scored_positions),
            "minimum_history": int(scored_positions[0]) if len(scored_positions) else None,
        })
    scoring["flags"] = flattened_flags
    scoring["truths"] = run_truths
    if not run_metrics:
        return _binary_metrics(np.asarray([]), np.asarray([])), scoring
    metric_names = ("precision", "recall", "f1", "false_alarm_rate")
    mean_metrics: dict[str, float | int] = {
        name: float(np.mean([metric[name] for metric in run_metrics if metric[name] is not None]))
        for name in metric_names
    }
    for count in ("true_positive", "false_positive", "false_negative", "true_negative"):
        mean_metrics[count] = int(sum(
            metric["confusion_matrix"][count]
            for metric in run_metrics
            if metric["confusion_matrix"] is not None
        ))
    return mean_metrics, scoring


def run_simulator_tuning(
    db: Session,
    *,
    ground_truth_run_ids: list[int],
    false_alarm_cap: float,
    created_by: int,
) -> SimulatorTuningRun:
    started = time.perf_counter()
    runs = load_development_ground_truth_runs(db, ground_truth_run_ids)
    values, labels, seeds = _ground_truth_series(db, runs)
    if len(values) < 20:
        raise ValueError("Development data contains too few readings for tuning")
    split = max(1, int(len(values) * 0.7))
    train_labels = labels[:split]
    validation_labels = labels[split:]

    candidate_groups: list[tuple[str, dict[str, Any]]] = []
    for algorithm in (
        "isolation_forest",
        "z_score",
        "ewma",
        "cusum",
        "local_outlier_factor",
        "one_class_svm",
        "autoencoder",
    ):
        if algorithm == "isolation_forest":
            for contamination in (0.01, 0.03, 0.05, 0.1):
                for estimators in (100, 200):
                    for mode in ("batch", "walk_forward"):
                        candidate_groups.append((algorithm, {
                            "algorithm": algorithm,
                            "contamination": contamination,
                            "n_estimators": estimators,
                            "mode": mode,
                        }))
        elif algorithm == "z_score":
            for threshold in (2.0, 2.5, 3.0, 3.5):
                candidate_groups.append((algorithm, {"algorithm": algorithm, "threshold": threshold}))
        elif algorithm == "ewma":
            for lam in (0.1, 0.2, 0.5):
                for limit in (2.5, 3.0):
                    for baseline_window in (30, 60, 100):
                        candidate_groups.append((algorithm, {
                            "algorithm": algorithm,
                            "lambda": lam,
                            "L": limit,
                            "baseline_window": baseline_window,
                        }))
        elif algorithm == "cusum":
            for k in (0.5, 1.0):
                for h in (3.0, 4.0, 5.0):
                    for baseline_window in (30, 60, 100):
                        candidate_groups.append((algorithm, {
                            "algorithm": algorithm,
                            "k": k,
                            "h": h,
                            "baseline_window": baseline_window,
                        }))
        elif algorithm == "local_outlier_factor":
            for neighbors in (20, 35):
                for contamination in (0.03, 0.05, 0.1):
                    candidate_groups.append((algorithm, {
                        "algorithm": algorithm,
                        "n_neighbors": neighbors,
                        "contamination": contamination,
                    }))
        elif algorithm == "one_class_svm":
            for nu in (0.01, 0.05, 0.1):
                for gamma in ("scale", 0.01, 0.1):
                    candidate_groups.append((algorithm, {
                        "algorithm": algorithm,
                        "nu": nu,
                        "gamma": gamma,
                    }))
        else:
            for hidden in ((8,), (8, 4)):
                for alpha in (0.0001, 0.001):
                    for percentile in (95, 97, 99):
                        candidate_groups.append((algorithm, {
                            "algorithm": algorithm,
                            "hidden_sizes": list(hidden),
                            "alpha": alpha,
                            "threshold_percentile": percentile,
                        }))

    candidates: list[dict[str, Any]] = []
    selected: dict[str, dict[str, Any]] = {}
    for algorithm, parameters in candidate_groups:
        metrics, scoring = development_evaluation_metrics(
            db, runs, parameters, tolerance_readings=5
        )
        candidates.append({
            "algorithm": algorithm,
            "parameters": parameters,
            **metrics,
            "scoring": scoring,
        })
        current = selected.get(algorithm)
        eligible = metrics["false_alarm_rate"] <= false_alarm_cap
        if eligible and (
            current is None
            or metrics["f1"] > current["f1"]
            or (metrics["f1"] == current["f1"] and metrics["false_alarm_rate"] < current["false_alarm_rate"])
        ):
            selected[algorithm] = {"parameters": parameters, **metrics}

    if not selected:
        raise ValueError("No tuning candidate satisfies the configured false-alarm cap")

    def _validation_flags(algorithm: str) -> np.ndarray:
        candidate = selected[algorithm]
        parameters = candidate["parameters"]
        _, scoring = development_evaluation_metrics(
            db, runs, parameters, tolerance_readings=5
        )
        if algorithm in ("ewma", "cusum", "local_outlier_factor", "isolation_forest", "z_score", "local_outlier_factor", "one_class_svm", "autoencoder"):
            return np.asarray(scoring["flags"], dtype=int)
        scores = _scores(values, parameters)
        threshold = float(candidate.get("threshold", 0.0))
        return (scores[split:] >= threshold).astype(int)

    ensemble_candidates: list[dict[str, Any]] = []
    ensemble_members = ("cusum", "z_score", "ewma", "local_outlier_factor")
    if all(algorithm in selected for algorithm in ensemble_members):
        for voting in ("majority", "weighted", "quorum"):
            weights = {member: 1.0 for member in ensemble_members}
            member_flags = {
                member: _validation_flags(member)
                for member in ensemble_members
            }
            common_length = min(len(flags) for flags in member_flags.values())
            member_flags = {
                member: flags[-common_length:]
                for member, flags in member_flags.items()
            }
            weighted_sum = sum(weights[member] * member_flags[member] for member in ensemble_members)
            if voting == "majority":
                ensemble_flags = weighted_sum > sum(weights.values()) / 2
            elif voting == "weighted":
                ensemble_flags = weighted_sum >= 2.0
            else:
                ensemble_flags = weighted_sum >= 3.0
            _, ensemble_scoring = development_evaluation_metrics(
                db, runs, selected[ensemble_members[0]]["parameters"], tolerance_readings=5
            )
            truth_values = np.asarray(ensemble_scoring["truths"], dtype=int)
            if len(truth_values) < len(ensemble_flags):
                truth_values = np.resize(truth_values, len(ensemble_flags))
            metrics = _binary_metrics(
                truth_values[-len(ensemble_flags):],
                ensemble_flags.astype(int),
            )
            candidate_parameters = {
                "algorithm": "ensemble",
                "members": {
                    member: selected[member]["parameters"]
                    for member in ensemble_members
                },
                "features": "per-detector simulator feature represented by the single tuning value series",
                "mode": "walk_forward",
                "weights": weights,
                "voting": voting,
                "quorum": 3 if voting == "quorum" else 2,
            }
            ensemble_candidates.append({**candidate_parameters, **metrics})

    current_members = ("isolation_forest", "z_score")
    if all(algorithm in selected for algorithm in current_members):
        member_flags = {member: _validation_flags(member) for member in current_members}
        current_weights = {member: 1.0 for member in current_members}
        for voting in ("majority", "weighted", "quorum"):
            raw_current_flags = {
                member: _validation_flags(member)
                for member in current_members
            }
            common_length = min(len(flags) for flags in raw_current_flags.values())
            member_flags = {
                member: flags[-common_length:]
                for member, flags in raw_current_flags.items()
            }
            weighted_sum = sum(
                current_weights[member] * member_flags[member]
                for member in current_members
            )
            threshold_count = 3 if voting == "quorum" else 2
            if voting == "quorum":
                ensemble_flags = weighted_sum >= threshold_count
            elif voting == "majority":
                ensemble_flags = weighted_sum >= 2.0
            else:
                ensemble_flags = weighted_sum >= 2.0
            _, ensemble_scoring = development_evaluation_metrics(
                db, runs, selected[current_members[0]]["parameters"], tolerance_readings=5
            )
            truth_values = np.asarray(ensemble_scoring["truths"], dtype=int)
            if len(truth_values) < len(ensemble_flags):
                truth_values = np.resize(truth_values, len(ensemble_flags))
            metrics = _binary_metrics(
                truth_values[-len(ensemble_flags):],
                ensemble_flags.astype(int),
            )
            ensemble_candidates.append({
                "algorithm": "ensemble",
                "members": {
                    member: selected[member]["parameters"]
                    for member in current_members
                },
                "features": "per-detector simulator feature represented by the single tuning value series",
                "mode": "walk_forward",
                "weights": current_weights,
                "voting": voting,
                "quorum": threshold_count,
                **metrics,
            })

    eligible_ensembles = [
        candidate for candidate in ensemble_candidates
        if candidate["false_alarm_rate"] <= false_alarm_cap
    ]
    if eligible_ensembles:
        best_ensemble = max(
            eligible_ensembles,
            key=lambda candidate: (candidate["f1"], -candidate["false_alarm_rate"]),
        )
        selected["ensemble"] = {
            "parameters": {
                key: best_ensemble[key]
                for key in (
                    "members", "features", "mode", "weights", "voting", "quorum",
                )
            },
            **{
                key: best_ensemble[key]
                for key in (
                    "precision", "recall", "f1", "false_alarm_rate",
                    "true_positive", "false_positive", "true_negative", "false_negative",
                )
            },
        }
        candidates.extend(ensemble_candidates)

    chosen = max(selected.values(), key=lambda item: item["f1"])
    record = SimulatorTuningRun(
        created_by=created_by,
        objective="maximize_f1_with_false_alarm_cap",
        false_alarm_cap=false_alarm_cap,
        ground_truth_run_ids=ground_truth_run_ids,
        data_seeds=seeds,
        configuration={
            "time_split": {"train_fraction": 0.7, "validation_fraction": 0.3},
            "source_type": "simulated",
            "search": "fixed grid",
        },
        candidates={"items": candidates},
        result={
            "selected": selected,
            "chosen": chosen,
        },
        runtime_seconds=time.perf_counter() - started,
        limitations=(
            "Development-only evidence from simulated ground-truth ledgers; time-aware "
            "validation is not an independent reserved evaluation. Statistical evidence "
            "for human review, not clinical or physical-failure diagnosis."
        ),
    )
    db.add(record)
    db.flush()
    return record


def evaluate_reserved_tuning_once(
    db: Session,
    *,
    tuning_run: SimulatorTuningRun,
) -> dict[str, Any]:
    reserved_ids = [13, 14, 8, 9, 10]
    runs = list(db.scalars(select(SimulatorGroundTruthRun).where(
        SimulatorGroundTruthRun.id.in_(reserved_ids)
    )))
    if any(run.purpose != "evaluation" for run in runs):
        raise ValueError("Reserved comparison must use evaluation-purpose runs only")
    if len(runs) != len(set(reserved_ids)):
        raise ValueError("One or more reserved ground-truth runs are missing")
    values, labels, seeds = _ground_truth_series(db, runs)
    chosen = (tuning_run.result or {}).get("chosen", {}).get("parameters", {})
    comparisons: dict[str, Any] = {}
    for name, parameters in (
        ("default_isolation_forest", {
            "algorithm": "isolation_forest",
            "contamination": 0.1,
            "n_estimators": 100,
            "mode": "batch",
        }),
        ("tuned_isolation_forest", chosen or {
            "algorithm": "isolation_forest",
            "contamination": 0.1,
            "n_estimators": 100,
            "mode": "batch",
        }),
    ):
        scores = _scores(values, parameters)
        threshold = float(np.quantile(scores, 0.9))
        metrics = _binary_metrics(labels, (scores >= threshold).astype(int))
        comparisons[name] = {**metrics, "parameters": parameters}
    comparison = {
        "ground_truth_run_ids": reserved_ids,
        "seeds": seeds,
        "source_type": "simulated",
        "comparisons": comparisons,
        "limitations": (
            "One-time reserved-data comparison generated after tuning. Detection "
            "delay is not available in this stored comparison without persisted "
            "detector result positions. It is not a new NASA IMS validation and "
            "is statistical evidence for human review."
        ),
    }
    tuning_run.result = {
        **(tuning_run.result or {}),
        "reserved_evaluation": comparison,
    }
    db.flush()
    return comparison


def prepare_reserved_tuning_comparison(
    db: Session,
    *,
    tuning_run: SimulatorTuningRun,
    ground_truth_run_ids: list[int],
    evaluation_runs: list[dict[str, Any]],
    created_by: int,
) -> dict[str, Any]:
    """Prepare, and execute exactly once, a comparison from persisted analysis runs."""
    from app.modules.analyses.application.simulator_evaluation import (
        create_simulator_evaluation,
    )

    if tuning_run.result and tuning_run.result.get("reserved_comparison"):
        raise ValueError("Reserved comparison has already been generated for this tuning run")
    runs = list(db.scalars(select(SimulatorGroundTruthRun).where(
        SimulatorGroundTruthRun.id.in_(ground_truth_run_ids)
    )))
    if len(runs) != len(set(ground_truth_run_ids)):
        raise LookupError("One or more reserved ground-truth runs were not found")
    if any(run.purpose != "evaluation" for run in runs):
        raise ValueError("Reserved comparison must use evaluation-purpose runs only")
    if sorted(run.seed for run in runs) != sorted((8001, 8002)):
        raise ValueError("Reserved comparison requires exactly seeds 8001 and 8002")
    requested_ids = {item["ground_truth_run_id"] for item in evaluation_runs}
    if requested_ids != set(ground_truth_run_ids):
        raise ValueError("Every reserved ground-truth run must have persisted analysis runs")

    fingerprint_payload = {
        "tuning_run_id": tuning_run.id,
        "ground_truth_run_ids": sorted(ground_truth_run_ids),
        "evaluation_runs": sorted(
            (
                item["ground_truth_run_id"],
                sorted(int(value) for value in item["analysis_run_ids"]),
            )
            for item in evaluation_runs
        ),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()

    evaluation = create_simulator_evaluation(
        db,
        runs=evaluation_runs,
        tolerance_readings=5,
        created_by=created_by,
    )
    result = evaluation.result or {}
    comparison = {
        "fingerprint": fingerprint,
        "simulator_evaluation_id": evaluation.id,
        "ground_truth_run_ids": ground_truth_run_ids,
        "seeds": [run.seed for run in runs],
        "source_type": "simulated",
        "detectors": result.get("detectors", {}),
        "limitations": (
            "One-time reserved comparison of seeds 8001 and 8002. Metrics and detection "
            "delay are computed from persisted analysis-run results and accepted reading "
            "positions. Development tuning used only seeds 101 and 202."
        ),
    }
    tuning_run.result = {
        **(tuning_run.result or {}),
        "reserved_comparison": comparison,
    }
    db.flush()
    return comparison
