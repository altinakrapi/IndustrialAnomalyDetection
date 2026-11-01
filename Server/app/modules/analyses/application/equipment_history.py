from typing import Any

import numpy as np

from app.modules.analyses.domain.detectors import (
    Detector,
    WalkForwardResult,
    resolve_ensemble_features,
    walk_forward_predict,
)


def score_equipment_history(
    detector: Detector,
    values: np.ndarray,
    *,
    parameters: dict[str, Any],
    feature_name: str,
    training_window: int,
    refit_every: int,
) -> list[dict[str, Any]]:
    matrix = np.asarray(values, dtype=np.float64).reshape(-1, 1)
    if not len(matrix) or not np.isfinite(matrix).all():
        raise ValueError("Equipment history must contain finite readings")
    if training_window < detector.minimum_history(parameters):
        raise ValueError("training_window must be at least minimum_history")
    if refit_every < 1:
        raise ValueError("refit_every must be positive")

    algorithm = detector.algorithm
    validated = detector.validate_config(parameters)
    if algorithm == "ensemble":
        validated = resolve_ensemble_features(validated, [feature_name])
    elif detector.uses_walk_forward:
        validated["refit_every"] = refit_every
        validated["training_window"] = training_window

    scored: dict[int, dict[str, Any]] = {}
    if detector.uses_walk_forward:
        result: WalkForwardResult = walk_forward_predict(
            detector,
            matrix,
            parameters={**validated, "training_window": training_window},
            missing_values_strategy="drop",
            scaling="standard",
            batch_size=refit_every,
        )
        for result_index, position in enumerate(result.positions):
            details = dict(result.result_details[result_index])
            details.update(
                {
                    "training_start_position": max(
                        0,
                        int(position) - int(result.history_sizes[result_index]),
                    ),
                    "training_end_position": int(position) - 1,
                }
            )
            state = (
                details.get("ensemble_state", "ready")
                if algorithm == "ensemble"
                else "ready"
            )
            scored[int(position)] = {
                "score": float(result.scores[result_index]),
                "is_anomaly": int(result.predictions[result_index]) == -1,
                "history_size": int(result.history_sizes[result_index]),
                "history_state": state,
                "score_threshold": float(result.score_thresholds[result_index]),
                "severity_thresholds": [
                    float(value)
                    for value in result.severity_thresholds[result_index]
                ],
                "details": details,
                "execution_mode": "walk_forward",
            }
        minimum_history = result.minimum_history
    else:
        minimum_history = detector.minimum_history(validated)
        for batch_start in range(minimum_history, len(matrix), refit_every):
            batch_end = min(batch_start + refit_every, len(matrix))
            history_start = max(0, batch_start - training_window)
            history = matrix[history_start:batch_start]
            model = detector.fit(
                history,
                parameters=validated,
                missing_values_strategy="drop",
                scaling="standard",
            )
            batch = matrix[batch_start:batch_end]
            predictions = detector.predict(model, batch)
            scores = detector.score(model, batch)
            reference = detector.history_score_reference(model)
            if reference is None or not len(reference):
                reference = detector.score(model, history)
            thresholds = (
                detector.decision_threshold(model),
                float(np.quantile(reference, 0.95)),
                float(np.quantile(reference, 0.995)),
            )
            details = detector.result_details(model, batch)
            for offset, position in enumerate(range(batch_start, batch_end)):
                scored[position] = {
                    "score": float(scores[offset]),
                    "is_anomaly": int(predictions[offset]) == -1,
                    "history_size": len(history),
                    "history_state": "ready",
                    "score_threshold": float(thresholds[0]),
                    "severity_thresholds": [
                        float(thresholds[1]),
                        float(thresholds[2]),
                    ],
                    "details": dict(details[offset]),
                    "execution_mode": "walk_forward_batch",
                    "training_start_position": history_start,
                    "training_end_position": batch_start - 1,
                }

    output: list[dict[str, Any]] = []
    for position in range(len(matrix)):
        item = scored.get(position)
        if item is None:
            output.append(
                {
                    "position": position,
                    "score": None,
                    "is_anomaly": False,
                    "history_size": position,
                    "history_state": "insufficient_history",
                    "history_required": minimum_history,
                    "score_threshold": None,
                    "severity_thresholds": None,
                    "details": {},
                    "execution_mode": (
                        "walk_forward"
                        if detector.uses_walk_forward
                        else "walk_forward_batch"
                    ),
                }
            )
            continue
        output.append(
            {
                "position": position,
                **item,
                "details": {
                    **item["details"],
                    "training_start_position": item.get(
                        "training_start_position",
                        item["details"].get("training_start_position"),
                    ),
                    "training_end_position": item.get(
                        "training_end_position",
                        item["details"].get("training_end_position"),
                    ),
                },
                "history_required": minimum_history,
            }
        )
    return output
