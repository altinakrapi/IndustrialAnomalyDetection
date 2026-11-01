from __future__ import annotations

from collections.abc import Sequence
from math import sqrt

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)


def label_readings(
    simulated_times: Sequence[float],
    intervals: Sequence[dict],
    tolerance_readings: int,
) -> tuple[list[bool], list[list[str]]]:
    if tolerance_readings < 0:
        raise ValueError("tolerance_readings must be non-negative")
    labels = [False] * len(simulated_times)
    fault_types: list[list[str]] = [[] for _ in simulated_times]
    for interval in intervals:
        matching_positions = [
            index
            for index, timestamp in enumerate(simulated_times)
            if interval["start_seconds"] <= timestamp <= interval["end_seconds"]
        ]
        if not matching_positions:
            continue
        last_position = matching_positions[-1]
        for index, timestamp in enumerate(simulated_times):
            in_interval = (
                interval["start_seconds"] <= timestamp <= interval["end_seconds"]
            )
            in_tolerance = (
                last_position < index <= last_position + tolerance_readings
            )
            if not (in_interval or in_tolerance):
                continue
            labels[index] = True
            if interval["type"] not in fault_types[index]:
                fault_types[index].append(interval["type"])
    return labels, fault_types


def binary_metrics(
    labels: Sequence[bool],
    predictions: Sequence[bool],
    scores: Sequence[float | None] | None = None,
) -> dict:
    if len(labels) != len(predictions):
        raise ValueError("labels and predictions must have equal lengths")
    if scores is not None and len(scores) != len(labels):
        raise ValueError("scores and labels must have equal lengths")
    if not labels:
        return {
            "sample_count": 0,
            "confusion_matrix": None,
            "precision": None,
            "recall": None,
            "f1": None,
            "false_alarm_rate": None,
            "roc_auc": None,
            "pr_auc": None,
            "roc_curve": [],
            "precision_recall_curve": [],
        }
    tp = sum(actual and predicted for actual, predicted in zip(labels, predictions))
    fp = sum(not actual and predicted for actual, predicted in zip(labels, predictions))
    fn = sum(actual and not predicted for actual, predicted in zip(labels, predictions))
    tn = sum(not actual and not predicted for actual, predicted in zip(labels, predictions))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    metrics = {
        "sample_count": len(labels),
        "confusion_matrix": {"true_positive": tp, "false_positive": fp,
                             "false_negative": fn, "true_negative": tn},
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0,
        "false_alarm_rate": fp / (fp + tn) if fp + tn else None,
        "roc_auc": None,
        "pr_auc": None,
        "roc_curve": [],
        "precision_recall_curve": [],
    }
    if scores is None:
        return metrics
    valid = [
        (bool(label), float(score))
        for label, score in zip(labels, scores)
        if score is not None and np.isfinite(score)
    ]
    if not valid:
        return metrics
    valid_labels, valid_scores = zip(*valid, strict=True)
    if len(set(valid_labels)) < 2:
        return metrics
    metrics["roc_auc"] = float(roc_auc_score(valid_labels, valid_scores))
    metrics["pr_auc"] = float(average_precision_score(valid_labels, valid_scores))
    fpr, tpr, _ = roc_curve(valid_labels, valid_scores)
    precision_values, recall_values, _ = precision_recall_curve(
        valid_labels, valid_scores
    )
    metrics["roc_curve"] = [
        {"false_positive_rate": float(x), "true_positive_rate": float(y)}
        for x, y in zip(fpr, tpr, strict=True)
    ]
    metrics["precision_recall_curve"] = [
        {"recall": float(x), "precision": float(y)}
        for x, y in zip(recall_values, precision_values, strict=True)
    ]
    return metrics


def aggregate_metric_runs(runs: Sequence[dict]) -> dict:
    metric_names = ("precision", "recall", "f1", "roc_auc", "pr_auc",
                    "false_alarm_rate")
    aggregate = {}
    for name in metric_names:
        values = [run[name] for run in runs if run.get(name) is not None]
        aggregate[name] = {
            "mean": sum(values) / len(values) if values else None,
            "spread_stddev": sqrt(
                sum((value - sum(values) / len(values)) ** 2 for value in values)
                / len(values)
            )
            if values
            else None,
            "run_count": len(values),
        }
    return aggregate
