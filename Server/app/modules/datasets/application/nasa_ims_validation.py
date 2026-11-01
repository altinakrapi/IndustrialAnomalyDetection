from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from io import StringIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.analyses.domain.detectors import (
    DETECTOR_REGISTRY,
    resolve_ensemble_features,
)
from app.modules.datasets.application.nasa_ims import CHANNEL_FEATURES
from app.shared.models import (
    AnalysisResult,
    AnalysisRun,
    Dataset,
    MeasurementRecord,
    ModelConfig,
    NasaIMSBearingFact,
    NasaIMSValidation,
)

LIMITATIONS = (
    "NASA IMS has no per-window ground-truth labels; no classification accuracy "
    "or Precision/Recall/F1 is claimed. Rates, sustained alerts and lead times "
    "are statistical evidence for human review, not a physical-failure diagnosis. "
    "The early-period false-alarm rate assumes the configured segment is healthy; "
    "that assumption cannot be verified without per-window labels. The primary "
    "sustained-alert view groups filename-derived snapshots and requires the "
    "configured consecutive flagged-snapshot streak. Legacy window-level streak "
    "results remain available but are a secondary view."
)


class NasaIMSValidationInProgress(ValueError):
    pass


def nasa_ims_test_set_for_dataset_name(name: str) -> str:
    for test_set in ("1st_test", "2nd_test", "3rd_test"):
        if re.search(rf"(?<![A-Za-z0-9]){test_set}(?![A-Za-z0-9])", name):
            return test_set
    raise ValueError("NASA IMS dataset name must identify 1st_test, 2nd_test or 3rd_test")


def features_for_bearing(
    test_set: str,
    channel_numbers: list[int],
) -> list[str]:
    if test_set not in {"1st_test", "2nd_test", "3rd_test"}:
        raise ValueError("Unsupported NASA IMS test set")
    prefix = "sensor" if test_set == "1st_test" else "bearing"
    return [
        f"{prefix}_{channel}_{feature}"
        for channel in channel_numbers
        for feature in CHANNEL_FEATURES
    ]


def analysis_features_for_detector(
    algorithm: str,
    bearing_features: list[str],
) -> list[str]:
    if algorithm in {"ewma", "cusum"}:
        rms_features = [
            feature for feature in bearing_features if feature.endswith("_rms")
        ]
        if not rms_features:
            raise ValueError(f"{algorithm} requires an RMS feature for the bearing")
        return [rms_features[0]]
    return bearing_features


def summarize_bearing_series(
    records: list[tuple[int, datetime]],
    scored_flags: dict[int, bool],
    *,
    healthy_period_fraction: float,
    sustained_alert_windows: int,
    time_bin_count: int,
    snapshot_flag_fraction: float = 0.2,
    sustained_alert_snapshots: int = 3,
    snapshot_burn_in_snapshots: int = 63,
) -> dict[str, Any]:
    if not records:
        raise ValueError("NASA IMS test set contains no measurement windows")
    if not 0 < healthy_period_fraction <= 0.5:
        raise ValueError("healthy_period_fraction must be greater than 0 and at most 0.5")
    if sustained_alert_windows < 1:
        raise ValueError("sustained_alert_windows must be positive")
    if time_bin_count < 1:
        raise ValueError("time_bin_count must be positive")
    if not 0 < snapshot_flag_fraction <= 1:
        raise ValueError("snapshot_flag_fraction must be greater than 0 and at most 1")
    if sustained_alert_snapshots < 1:
        raise ValueError("sustained_alert_snapshots must be positive")

    normalized_records: list[tuple[int, datetime, str]] = []
    for item in records:
        if len(item) == 2:
            normalized_records.append((item[0], item[1], f"window-{item[0]}"))
        else:
            normalized_records.append((item[0], item[1], str(item[2])))
    ordered = sorted(normalized_records, key=lambda item: item[0])
    start = ordered[0][1]
    end = ordered[-1][1]
    elapsed = max(0.0, (end - start).total_seconds())
    healthy_end = start + (end - start) * healthy_period_fraction
    scored_count = 0
    flagged_count = 0
    healthy_scored = 0
    healthy_flagged = 0
    streak = 0
    previous_position: int | None = None
    first_sustained_alert: tuple[int, datetime] | None = None
    bins = [
        {
            "bin": index,
            "start_at": (
                start + (end - start) * (index / time_bin_count)
            ).isoformat(),
            "end_at": (
                start + (end - start) * ((index + 1) / time_bin_count)
            ).isoformat(),
            "scored_windows": 0,
            "flagged_windows": 0,
            "anomaly_rate": None,
        }
        for index in range(time_bin_count)
    ]
    snapshot_groups: dict[str, list[tuple[int, datetime, bool]]] = {}
    snapshot_timestamps: dict[str, datetime] = {}
    for position, timestamp, snapshot in ordered:
        is_scored = position in scored_flags
        if not is_scored:
            streak = 0
            previous_position = position
            continue
        flagged = bool(scored_flags[position])
        snapshot_groups.setdefault(snapshot, []).append((position, timestamp, flagged))
        snapshot_timestamps[snapshot] = timestamp
        scored_count += 1
        flagged_count += int(flagged)
        if timestamp <= healthy_end:
            healthy_scored += 1
            healthy_flagged += int(flagged)
        if flagged:
            streak = streak + 1 if previous_position == position - 1 else 1
            if streak >= sustained_alert_windows and first_sustained_alert is None:
                first_sustained_alert = (position, timestamp)
        else:
            streak = 0
        previous_position = position
        if elapsed == 0:
            bin_index = 0
        else:
            bin_index = min(
                int(((timestamp - start).total_seconds() / elapsed) * time_bin_count),
                time_bin_count - 1,
            )
        bins[bin_index]["scored_windows"] += 1
        bins[bin_index]["flagged_windows"] += int(flagged)
    for item in bins:
        count = item["scored_windows"]
        if count:
            item["anomaly_rate"] = item["flagged_windows"] / count

    lead_time_seconds = (
        max(0.0, (end - first_sustained_alert[1]).total_seconds())
        if first_sustained_alert
        else None
    )
    snapshot_summaries = []
    snapshot_streak = 0
    first_snapshot_alert: tuple[str, datetime] | None = None
    snapshot_healthy_scored = 0
    snapshot_healthy_flagged = 0
    for snapshot in sorted(snapshot_groups, key=lambda key: snapshot_timestamps[key]):
        windows = snapshot_groups[snapshot]
        flagged_windows = sum(int(flagged) for _, _, flagged in windows)
        flagged_share = flagged_windows / len(windows)
        snapshot_flagged = flagged_share >= snapshot_flag_fraction
        snapshot_timestamp = snapshot_timestamps[snapshot]
        snapshot_summaries.append({
            "snapshot": snapshot,
            "timestamp": snapshot_timestamp.isoformat(),
            "scored_windows": len(windows),
            "flagged_windows": flagged_windows,
            "flagged_share": flagged_share,
            "flagged": snapshot_flagged,
        })
        if snapshot_timestamp <= healthy_end:
            snapshot_healthy_scored += 1
            snapshot_healthy_flagged += int(snapshot_flagged)
        if snapshot_flagged:
            snapshot_streak += 1
            if snapshot_streak >= sustained_alert_snapshots and first_snapshot_alert is None:
                first_snapshot_alert = (snapshot, snapshot_timestamp)
        else:
            snapshot_streak = 0
    snapshot_lead_seconds = (
        max(0.0, (end - first_snapshot_alert[1]).total_seconds())
        if first_snapshot_alert else None
    )
    filtered_snapshots = snapshot_summaries[snapshot_burn_in_snapshots:]
    filtered_streak = 0
    first_filtered_alert: tuple[str, datetime] | None = None
    for snapshot_summary in filtered_snapshots:
        if snapshot_summary["flagged"]:
            filtered_streak += 1
            if (
                filtered_streak >= sustained_alert_snapshots
                and first_filtered_alert is None
            ):
                first_filtered_alert = (
                    snapshot_summary["snapshot"],
                    datetime.fromisoformat(snapshot_summary["timestamp"]),
                )
        else:
            filtered_streak = 0
    filtered_lead_seconds = (
        max(0.0, (end - first_filtered_alert[1]).total_seconds())
        if first_filtered_alert else None
    )
    filtered_healthy_count = (
        max(1, int(len(filtered_snapshots) * healthy_period_fraction))
        if filtered_snapshots else 0
    )
    filtered_healthy_snapshots = filtered_snapshots[:filtered_healthy_count]
    filtered_healthy_flagged = sum(
        1 for item in filtered_healthy_snapshots if item["flagged"]
    )
    return {
        "window_count": len(ordered),
        "scored_windows": scored_count,
        "unscored_windows": len(ordered) - scored_count,
        "flagged_windows": flagged_count,
        "overall_anomaly_rate": (
            flagged_count / scored_count if scored_count else None
        ),
        "healthy_period": {
            "fraction_of_test": healthy_period_fraction,
            "end_at": healthy_end.isoformat(),
            "scored_windows": healthy_scored,
            "flagged_windows": healthy_flagged,
            "false_alarm_rate": (
                healthy_flagged / healthy_scored if healthy_scored else None
            ),
        },
        "sustained_alert": {
            "required_consecutive_windows": sustained_alert_windows,
            "detected": first_sustained_alert is not None,
            "first_detected_source_row": (
                first_sustained_alert[0] if first_sustained_alert else None
            ),
            "first_detected_at": (
                first_sustained_alert[1].isoformat()
                if first_sustained_alert
                else None
            ),
        },
        "lead_time_to_test_end_seconds": lead_time_seconds,
        "lead_time_to_test_end_hours": (
            lead_time_seconds / 3600 if lead_time_seconds is not None else None
        ),
        "snapshot_aggregation": {
            "flagged_share_threshold": snapshot_flag_fraction,
            "required_consecutive_snapshots": sustained_alert_snapshots,
            "snapshot_count": len(snapshot_summaries),
            "snapshots": snapshot_summaries,
            "sustained_alert": {
                "detected": first_filtered_alert is not None,
                "first_snapshot": first_filtered_alert[0] if first_filtered_alert else None,
                "first_detected_at": first_filtered_alert[1].isoformat() if first_filtered_alert else None,
            },
            "healthy_period_false_alarm_proxy": {
                "scored_snapshots": len(filtered_healthy_snapshots),
                "flagged_snapshots": filtered_healthy_flagged,
                "false_alarm_rate": (
                    filtered_healthy_flagged / len(filtered_healthy_snapshots)
                    if filtered_healthy_snapshots else None
                ),
            },
            "lead_time_to_test_end_seconds": filtered_lead_seconds,
            "lead_time_to_test_end_hours": (
                filtered_lead_seconds / 3600 if filtered_lead_seconds is not None else None
            ),
        },
        "snapshot_burn_in": {
            "configured_snapshots": snapshot_burn_in_snapshots,
            "filtered_snapshot_count": len(filtered_snapshots),
            "unfiltered_view": {
                "sustained_alert": {
                    "detected": first_snapshot_alert is not None,
                    "first_snapshot": first_snapshot_alert[0] if first_snapshot_alert else None,
                    "first_detected_at": first_snapshot_alert[1].isoformat() if first_snapshot_alert else None,
                },
                "lead_time_to_test_end_seconds": snapshot_lead_seconds,
                "lead_time_to_test_end_hours": (
                    snapshot_lead_seconds / 3600 if snapshot_lead_seconds is not None else None
                ),
                "healthy_period_false_alarm_proxy": {
                    "scored_snapshots": snapshot_healthy_scored,
                    "flagged_snapshots": snapshot_healthy_flagged,
                    "false_alarm_rate": (
                        snapshot_healthy_flagged / snapshot_healthy_scored
                        if snapshot_healthy_scored else None
                    ),
                },
            },
            "sustained_alert": {
                "detected": first_filtered_alert is not None,
                "first_snapshot": first_filtered_alert[0] if first_filtered_alert else None,
                "first_detected_at": first_filtered_alert[1].isoformat() if first_filtered_alert else None,
            },
            "lead_time_to_test_end_seconds": filtered_lead_seconds,
            "lead_time_to_test_end_hours": (
                filtered_lead_seconds / 3600 if filtered_lead_seconds is not None else None
            ),
            "healthy_period_false_alarm_proxy": {
                "scored_snapshots": len(filtered_healthy_snapshots),
                "flagged_snapshots": filtered_healthy_flagged,
                "false_alarm_rate": (
                    filtered_healthy_flagged / len(filtered_healthy_snapshots)
                    if filtered_healthy_snapshots else None
                ),
            },
        },
        "time_bins": bins,
        "test_start_at": start.isoformat(),
        "test_end_at": end.isoformat(),
    }


def create_nasa_ims_validation(
    db: Session,
    *,
    dataset_id: int,
    healthy_period_fraction: float,
    sustained_alert_windows: int,
    time_bin_count: int,
    created_by: int,
    snapshot_flag_fraction: float = 0.2,
    sustained_alert_snapshots: int = 3,
    snapshot_burn_in_snapshots: int = 63,
) -> NasaIMSValidation:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise LookupError("NASA IMS dataset was not found")
    if dataset.file_format != "nasa_ims":
        raise ValueError("Validation is available only for NASA IMS datasets")
    if dataset.status != "uploaded":
        raise ValueError("NASA IMS dataset must be fully imported before validation")
    active_validation = db.scalar(
        select(NasaIMSValidation.id).where(
            NasaIMSValidation.dataset_id == dataset.id,
            NasaIMSValidation.status.in_(("queued", "running")),
        )
    )
    if active_validation is not None:
        raise NasaIMSValidationInProgress(
            f"NASA IMS validation {active_validation} is already active for this dataset"
        )
    if not 0 < healthy_period_fraction <= 0.5:
        raise ValueError("healthy_period_fraction must be greater than 0 and at most 0.5")
    if not 1 <= sustained_alert_windows <= 100:
        raise ValueError("sustained_alert_windows must be between 1 and 100")
    if not 10 <= time_bin_count <= 500:
        raise ValueError("time_bin_count must be between 10 and 500")
    if not 0 < snapshot_flag_fraction <= 1:
        raise ValueError("snapshot_flag_fraction must be greater than 0 and at most 1")
    if not 1 <= sustained_alert_snapshots <= 100:
        raise ValueError("sustained_alert_snapshots must be between 1 and 100")
    if not 0 <= snapshot_burn_in_snapshots <= 983:
        raise ValueError("snapshot_burn_in_snapshots must be between 0 and 983")

    test_set = nasa_ims_test_set_for_dataset_name(dataset.name)
    facts = list(
        db.scalars(
            select(NasaIMSBearingFact)
            .where(NasaIMSBearingFact.test_set == test_set)
            .order_by(NasaIMSBearingFact.bearing_number)
        )
    )
    if len(facts) != 4:
        raise ValueError(f"Verified bearing facts are unavailable for {test_set}")
    feature_names = {item["name"] for item in dataset.column_metadata}
    bearing_features: dict[int, list[str]] = {}
    for fact in facts:
        selected = features_for_bearing(test_set, fact.channel_numbers)
        missing = [feature for feature in selected if feature not in feature_names]
        if missing:
            raise ValueError(
                f"{test_set} bearing {fact.bearing_number} is missing imported "
                f"features: {missing}"
            )
        bearing_features[fact.bearing_number] = selected

    configuration: dict[str, Any] = {
        "test_set": test_set,
        "source_type": "dataset",
        "healthy_period_fraction": healthy_period_fraction,
        "sustained_alert_windows": sustained_alert_windows,
        "time_bin_count": time_bin_count,
        "snapshot_flag_fraction": snapshot_flag_fraction,
        "sustained_alert_snapshots": sustained_alert_snapshots,
        "snapshot_burn_in_snapshots": snapshot_burn_in_snapshots,
        "detector_settings": {},
        "bearing_features": {},
    }
    validation = NasaIMSValidation(
        dataset_id=dataset.id,
        created_by=created_by,
        status="queued",
        configuration=configuration,
        analysis_run_ids=[],
        result={},
        limitations=LIMITATIONS,
    )
    db.add(validation)
    db.flush()
    configuration = {
        **configuration,
        "detector_settings": {},
        "bearing_features": {},
        "analysis_features": {},
    }

    analysis_specs: list[dict[str, Any]] = []
    for algorithm, detector_class in DETECTOR_REGISTRY.items():
        detector_settings = detector_class.validate_config({})
        configuration["detector_settings"][algorithm] = detector_settings
        for fact in facts:
            selected_features = analysis_features_for_detector(
                algorithm,
                bearing_features[fact.bearing_number],
            )
            configuration["analysis_features"].setdefault(
                algorithm, {}
            )[str(fact.bearing_number)] = selected_features
            config_settings = dict(detector_settings)
            if algorithm == "ensemble":
                config_settings = resolve_ensemble_features(
                    config_settings, selected_features
                )
            config = ModelConfig(
                name=(
                    f"NASA IMS validation {detector_class.display_name} "
                    f"({validation.id})"
                )[:150],
                algorithm=algorithm,
                description=(
                    "Historical NASA IMS per-bearing validation. Results are "
                    "statistical evidence for human review, not a diagnosis."
                ),
                parameters=config_settings,
                contamination=(
                    config_settings.get("contamination")
                    if algorithm == "isolation_forest"
                    and isinstance(config_settings.get("contamination"), (int, float))
                    else None
                ),
                created_by=created_by,
            )
            db.add(config)
            db.flush()
            analysis = AnalysisRun(
                dataset_id=dataset.id,
                model_config_id=config.id,
                started_by=created_by,
                status="pending",
                source_type="dataset",
                metrics={
                    "configuration": {
                        "model_config_id": config.id,
                        "model_name": config.name,
                        "algorithm": algorithm,
                        "parameters": config_settings,
                        "features": selected_features,
                        "missing_values_strategy": "median",
                        "scaling": "standard",
                        "nasa_ims_validation_id": validation.id,
                        "bearing_number": fact.bearing_number,
                        "nasa_ims_test_set": test_set,
                        "suppress_notifications": True,
                    }
                },
            )
            db.add(analysis)
            db.flush()
            analysis_specs.append(
                {
                    "algorithm": algorithm,
                    "bearing_number": fact.bearing_number,
                    "analysis_run_id": analysis.id,
                    "settings": config_settings,
                }
            )
    if test_set == "2nd_test":
        for fact in facts:
            detector_class = DETECTOR_REGISTRY["isolation_forest"]
            selected_features = [
                f"bearing_{bearing_number}_{feature}"
                for feature in CHANNEL_FEATURES
                for bearing_number in range(1, 5)
            ]
            detector_settings = dict(configuration["detector_settings"]["isolation_forest"])
            config = ModelConfig(
                name=f"NASA IMS 36-feature Isolation Forest ({validation.id}-{fact.bearing_number})"[:150],
                algorithm="isolation_forest",
                description="Additional NASA IMS multi-feature configuration; no detector threshold tuning.",
                parameters=detector_settings,
                contamination=(
                    detector_settings.get("contamination")
                    if isinstance(detector_settings.get("contamination"), (int, float))
                    else None
                ),
                created_by=created_by,
            )
            db.add(config)
            db.flush()
            analysis = AnalysisRun(
                dataset_id=dataset.id,
                model_config_id=config.id,
                started_by=created_by,
                status="pending",
                source_type="dataset",
                metrics={
                    "configuration": {
                        "model_config_id": config.id,
                        "model_name": config.name,
                        "algorithm": "isolation_forest",
                        "parameters": detector_settings,
                        "features": selected_features,
                        "feature_configuration": "multi_feature_bearing_channel_36",
                        "missing_values_strategy": "median",
                        "scaling": "standard",
                        "nasa_ims_validation_id": validation.id,
                        "bearing_number": fact.bearing_number,
                        "nasa_ims_test_set": test_set,
                        "suppress_notifications": True,
                    }
                },
            )
            db.add(analysis)
            db.flush()
            configuration["analysis_features"].setdefault(
                "isolation_forest_multi_feature", {}
            )[str(fact.bearing_number)] = selected_features
            analysis_specs.append({
                "algorithm": "isolation_forest_multi_feature",
                "bearing_number": fact.bearing_number,
                "analysis_run_id": analysis.id,
                "settings": detector_settings,
            })
    configuration["bearing_features"] = {
        str(number): features
        for number, features in bearing_features.items()
    }
    configuration["analyses"] = analysis_specs
    validation.configuration = configuration
    validation.analysis_run_ids = [
        item["analysis_run_id"] for item in analysis_specs
    ]
    db.flush()
    return validation


def build_nasa_ims_validation_result(
    db: Session,
    validation: NasaIMSValidation,
) -> dict[str, Any]:
    dataset = db.get(Dataset, validation.dataset_id)
    if dataset is None:
        raise LookupError("NASA IMS dataset was not found")
    test_set = validation.configuration["test_set"]
    facts = list(
        db.scalars(
            select(NasaIMSBearingFact)
            .where(NasaIMSBearingFact.test_set == test_set)
            .order_by(NasaIMSBearingFact.bearing_number)
        )
    )
    records = [
        (
            int(record.source_row_number),
            record.timestamp,
            (record.values or {}).get("source_file", f"window-{record.source_row_number}"),
        )
        for record in db.scalars(
            select(MeasurementRecord)
            .where(MeasurementRecord.dataset_id == dataset.id)
            .order_by(MeasurementRecord.source_row_number)
        )
    ]
    if not records:
        raise ValueError("NASA IMS dataset has no imported measurement windows")
    if any(timestamp is None for _, timestamp, _ in records):
        raise ValueError("NASA IMS validation requires timestamps on every window")
    ordered_records = [
        (int(source_row_number), timestamp, source_file)
        for source_row_number, timestamp, source_file in records
    ]
    per_detector: dict[str, dict[str, Any]] = {}
    for spec in validation.configuration["analyses"]:
        analysis = db.get(AnalysisRun, spec["analysis_run_id"])
        if analysis is None or analysis.status != "completed":
            raise ValueError(
                f"Analysis run {spec['analysis_run_id']} did not complete successfully"
            )
        result_rows = db.execute(
            select(
                AnalysisResult.source_row_number,
                AnalysisResult.is_anomaly,
                AnalysisResult.history_state,
            )
            .where(AnalysisResult.analysis_run_id == analysis.id)
            .order_by(AnalysisResult.source_row_number)
        )
        flags = {
            int(row_number): bool(is_anomaly)
            for row_number, is_anomaly, history_state in result_rows
            if row_number is not None
            and history_state not in {"insufficient_history", "excluded_missing_values"}
        }
        bearing_metrics = summarize_bearing_series(
            ordered_records,
            flags,
            healthy_period_fraction=validation.configuration[
                "healthy_period_fraction"
            ],
            sustained_alert_windows=validation.configuration[
                "sustained_alert_windows"
            ],
            time_bin_count=validation.configuration["time_bin_count"],
            snapshot_flag_fraction=validation.configuration.get(
                "snapshot_flag_fraction", 0.2
            ),
            sustained_alert_snapshots=validation.configuration.get(
                "sustained_alert_snapshots", 3
            ),
            snapshot_burn_in_snapshots=validation.configuration.get(
                "snapshot_burn_in_snapshots", 63
            ),
        )
        algorithm = spec["algorithm"]
        display_algorithm = (
            "isolation_forest"
            if algorithm == "isolation_forest_multi_feature" else algorithm
        )
        detector = per_detector.setdefault(
            algorithm,
            {
                "display_name": (
                    f"{DETECTOR_REGISTRY[display_algorithm].display_name} "
                    "(36-feature bearing configuration)"
                    if algorithm == "isolation_forest_multi_feature"
                    else DETECTOR_REGISTRY[algorithm].display_name
                ),
                "execution_mode": (
                    "walk_forward"
                    if DETECTOR_REGISTRY[display_algorithm].uses_walk_forward
                    else "batch"
                ),
                "bearings": {},
                "analysis_run_ids": [],
            },
        )
        if algorithm == "isolation_forest_multi_feature":
            detector["attribution"] = "shared_across_bearings"
            detector["attribution_disclaimer"] = (
                "One model and one window-level flag set are shared by all four "
                "bearings; these results are not attributable per bearing."
            )
        if algorithm == "ensemble":
            detector["ensemble_member_configuration"] = (
                analysis.metrics["configuration"]["parameters"]["members"]
            )
        detector["bearings"][str(spec["bearing_number"])] = bearing_metrics
        detector["analysis_run_ids"].append(analysis.id)

    comparisons: dict[str, Any] = {}
    for algorithm, detector in per_detector.items():
        bearing_leads = {}
        for bearing_number, metrics in detector["bearings"].items():
            lead = metrics["snapshot_aggregation"]["lead_time_to_test_end_hours"]
            bearing_leads[int(bearing_number)] = lead
        failed_lead = bearing_leads.get(1)
        healthy_leads = [
            lead for bearing_number, lead in bearing_leads.items()
            if bearing_number != 1 and lead is not None
        ]
        detector["separation"] = {
            "interpretation": (
                "Descriptive bearing separation after snapshot burn-in; "
                "not a statistical test, accuracy metric, or physical diagnosis."
            ),
            "bearing_1_lead_hours": failed_lead,
            "earliest_healthy_bearing_lead_hours": (
                min(healthy_leads) if healthy_leads else None
            ),
            "mean_healthy_bearing_lead_hours": (
                sum(healthy_leads) / len(healthy_leads) if healthy_leads else None
            ),
            "lead_vs_earliest_healthy_hours": (
                failed_lead - min(healthy_leads)
                if failed_lead is not None and healthy_leads else None
            ),
            "lead_vs_mean_healthy_hours": (
                failed_lead - sum(healthy_leads) / len(healthy_leads)
                if failed_lead is not None and healthy_leads else None
            ),
            "healthy_sustained_alerts": {
                str(bearing_number): detector["bearings"][
                    str(bearing_number)
                ]["snapshot_aggregation"]["sustained_alert"]["detected"]
                for bearing_number in (2, 3, 4)
                if str(bearing_number) in detector["bearings"]
            },
            "healthy_bearings_reached_sustained_alert": sum(
                bool(detector["bearings"][str(bearing_number)]["snapshot_aggregation"]["sustained_alert"]["detected"])
                for bearing_number in (2, 3, 4)
                if str(bearing_number) in detector["bearings"]
            ),
        }
        if algorithm == "isolation_forest_multi_feature":
            comparisons[algorithm] = {
                "attribution": "not_attributable_per_bearing",
                "interpretation": (
                    "The 36-feature Isolation Forest is fitted on all four bearings "
                    "as one matrix and produces one shared window flag; it has no "
                    "failed-versus-healthy bearing comparison."
                ),
            }
            continue
        failed_facts = [fact for fact in facts if fact.is_failed]
        healthy_facts = [fact for fact in facts if not fact.is_failed]
        comparisons[algorithm] = {}
        for failed in failed_facts:
            failed_rate = detector["bearings"][str(failed.bearing_number)][
                "overall_anomaly_rate"
            ]
            healthy_rates = [
                detector["bearings"][str(fact.bearing_number)][
                    "overall_anomaly_rate"
                ]
                for fact in healthy_facts
            ]
            valid_healthy_rates = [rate for rate in healthy_rates if rate is not None]
            comparisons[algorithm][str(failed.bearing_number)] = {
                "failed_bearing_anomaly_rate": failed_rate,
                "healthy_bearing_rates": {
                    str(fact.bearing_number): detector["bearings"][
                        str(fact.bearing_number)
                    ]["overall_anomaly_rate"]
                    for fact in healthy_facts
                },
                "mean_healthy_bearing_anomaly_rate": (
                    sum(valid_healthy_rates) / len(valid_healthy_rates)
                    if valid_healthy_rates
                    else None
                ),
                "higher_than_every_healthy_bearing": (
                    failed_rate > max(valid_healthy_rates)
                    if failed_rate is not None and valid_healthy_rates
                    else None
                ),
                "interpretation": (
                    "Descriptive rate comparison only; not a statistical test or "
                    "classification accuracy."
                ),
            }
    return {
        "source_type": "dataset",
        "dataset_id": dataset.id,
        "dataset_name": dataset.name,
        "test_set": test_set,
        "window_count": len(ordered_records),
        "test_start_at": ordered_records[0][1].isoformat(),
        "test_end_at": ordered_records[-1][1].isoformat(),
        "verified_bearing_facts": [
            {
                "bearing_number": fact.bearing_number,
                "channel_numbers": fact.channel_numbers,
                "is_failed": fact.is_failed,
                "failure_type": fact.failure_type,
                "healthy_bearing_count": fact.healthy_bearing_count,
                "failed_bearing_count": fact.failed_bearing_count,
                "source_document": fact.source_document,
                "source_section": fact.source_section,
                "source_excerpt": fact.source_excerpt,
            }
            for fact in facts
        ],
        "detectors": per_detector,
        "failed_vs_healthy_rate_comparisons": comparisons,
        "limitations": LIMITATIONS,
    }


def nasa_ims_validation_to_csv(validation: NasaIMSValidation) -> str:
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "record_type",
            "test_set",
            "detector",
            "execution_mode",
            "bearing_number",
            "is_failed_bearing",
            "failure_type",
            "overall_anomaly_rate",
            "healthy_period_fraction",
            "healthy_period_false_alarm_rate",
            "healthy_period_flagged_windows",
            "healthy_period_scored_windows",
            "sustained_alert_detected",
            "sustained_alert_windows",
            "first_sustained_alert_at",
            "lead_time_to_test_end_seconds",
            "snapshot_flagged_share_threshold",
            "snapshot_required_consecutive_snapshots",
            "snapshot_sustained_alert_detected",
            "snapshot_first_sustained_alert_at",
            "snapshot_lead_time_to_test_end_seconds",
            "snapshot_healthy_period_false_alarm_rate",
            "snapshot_burn_in_snapshots",
            "snapshot_flagged_share",
            "separation_lead_vs_earliest_healthy_hours",
            "separation_lead_vs_mean_healthy_hours",
            "healthy_bearings_reached_sustained_alert",
            "separation_interpretation",
            "bearing_attribution",
            "window_sustained_alert_view",
            "scored_windows",
            "flagged_windows",
            "time_bin",
            "time_bin_start",
            "time_bin_end",
            "time_bin_anomaly_rate",
            "limitations",
            "ensemble_member_configuration",
            "result_status",
        ],
    )
    writer.writeheader()
    if validation.result:
        for algorithm, detector in validation.result.get("detectors", {}).items():
            for bearing_number, metrics in detector["bearings"].items():
                fact = next(
                    item
                    for item in validation.result["verified_bearing_facts"]
                    if item["bearing_number"] == int(bearing_number)
                )
                burn_in = metrics["snapshot_burn_in"]["configured_snapshots"]
                filtered_snapshots = metrics["snapshot_aggregation"].get(
                    "snapshots", []
                )[burn_in:]
                common = {
                    "test_set": validation.result["test_set"],
                    "detector": algorithm,
                    "execution_mode": detector["execution_mode"],
                    "bearing_number": bearing_number,
                    "is_failed_bearing": fact["is_failed"],
                    "failure_type": fact["failure_type"],
                    "bearing_attribution": (
                        "not_attributable_per_bearing"
                        if algorithm == "isolation_forest_multi_feature"
                        else "per_bearing"
                    ),
                    "ensemble_member_configuration": (
                        json.dumps(detector.get("ensemble_member_configuration", []))
                        if algorithm == "ensemble" else ""
                    ),
                    "result_status": detector.get("status", "valid"),
                    "overall_anomaly_rate": metrics["overall_anomaly_rate"],
                    "healthy_period_fraction": metrics["healthy_period"][
                        "fraction_of_test"
                    ],
                    "healthy_period_false_alarm_rate": metrics["healthy_period"][
                        "false_alarm_rate"
                    ],
                    "healthy_period_flagged_windows": metrics["healthy_period"][
                        "flagged_windows"
                    ],
                    "healthy_period_scored_windows": metrics["healthy_period"][
                        "scored_windows"
                    ],
                    "sustained_alert_detected": metrics["sustained_alert"][
                        "detected"
                    ],
                    "sustained_alert_windows": metrics["sustained_alert"][
                        "required_consecutive_windows"
                    ],
                    "first_sustained_alert_at": metrics["sustained_alert"][
                        "first_detected_at"
                    ],
                    "lead_time_to_test_end_seconds": metrics[
                        "lead_time_to_test_end_seconds"
                    ],
                    "snapshot_flagged_share_threshold": metrics[
                        "snapshot_aggregation"
                    ]["flagged_share_threshold"],
                    "snapshot_required_consecutive_snapshots": metrics[
                        "snapshot_aggregation"
                    ]["required_consecutive_snapshots"],
                    "snapshot_sustained_alert_detected": metrics[
                        "snapshot_aggregation"
                    ]["sustained_alert"]["detected"],
                    "snapshot_first_sustained_alert_at": metrics[
                        "snapshot_aggregation"
                    ]["sustained_alert"]["first_detected_at"],
                    "snapshot_lead_time_to_test_end_seconds": metrics[
                        "snapshot_aggregation"
                    ]["lead_time_to_test_end_seconds"],
                    "snapshot_healthy_period_false_alarm_rate": metrics[
                        "snapshot_aggregation"
                    ]["healthy_period_false_alarm_proxy"]["false_alarm_rate"],
                    "snapshot_burn_in_snapshots": metrics[
                        "snapshot_burn_in"
                    ]["configured_snapshots"],
                    "snapshot_flagged_share": (
                        (
                            sum(1 for snapshot in filtered_snapshots if snapshot["flagged"])
                            / len(filtered_snapshots)
                        )
                        if filtered_snapshots else None
                    ),
                    "healthy_bearings_reached_sustained_alert": (
                        "N/A_not_attributable_per_bearing"
                        if algorithm == "isolation_forest_multi_feature"
                        else sum(
                            bool(detected)
                            for detected in detector.get("separation", {}).get(
                                "healthy_sustained_alerts", {}
                            ).values()
                        )
                    ),
                    "separation_lead_vs_earliest_healthy_hours": detector.get(
                        "separation", {}
                    ).get("lead_vs_earliest_healthy_hours"),
                    "separation_lead_vs_mean_healthy_hours": detector.get(
                        "separation", {}
                    ).get("lead_vs_mean_healthy_hours"),
                    "separation_interpretation": detector.get(
                        "separation", {}
                    ).get("interpretation"),
                    "window_sustained_alert_view": "secondary",
                    "scored_windows": metrics["scored_windows"],
                    "flagged_windows": metrics["flagged_windows"],
                    "limitations": validation.limitations,
                }
                writer.writerow({"record_type": "bearing_summary", **common})
                for time_bin in metrics["time_bins"]:
                    writer.writerow(
                        {
                            "record_type": "time_bin",
                            **common,
                            "time_bin": time_bin["bin"],
                            "time_bin_start": time_bin["start_at"],
                            "time_bin_end": time_bin["end_at"],
                            "time_bin_anomaly_rate": time_bin["anomaly_rate"],
                        }
                    )
    return output.getvalue()
