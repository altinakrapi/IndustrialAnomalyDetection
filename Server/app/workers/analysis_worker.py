import logging
import math
import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from scipy.stats import norm
from sqlalchemy import delete, insert, select

from app.core.config import settings
from app.core.notifications import notify_user, notify_users_batch
from app.modules.analyses.domain.detectors import (
    DETECTOR_REGISTRY,
    Detector,
    ZScoreDetector,
    get_detector,
    normalize_algorithm_name,
    resolve_ensemble_features,
    walk_forward_predict,
)
from app.modules.datasets.application.nasa_ims_model import (
    is_compatible_nasa_ims_schema,
    model_is_available,
    predict_nasa_ims_matrix,
)
from app.shared.database.session import SessionLocal
from app.shared.feature_selection import is_analysis_feature
from app.shared.models import (
    AnalysisRun,
    AnalysisResult,
    Anomaly,
    Dataset,
    MeasurementRecord,
    ModelConfig,
    User,
)

logger = logging.getLogger(__name__)

PERSISTENCE_BATCH_SIZE = 2000


def analysis_notifications_enabled(configuration: dict) -> bool:
    return not bool(configuration.get("suppress_notifications", False))


def _insert_values(model: type, instance: object) -> dict:
    state = vars(instance)
    return {
        column.key: state[column.key]
        for column in model.__table__.columns
        if column.key != "id" and column.key in state
    }


def _bulk_persist_analysis_rows(
    db,
    results: list[AnalysisResult],
    anomalies: list[Anomaly],
) -> None:
    for offset in range(0, len(results), PERSISTENCE_BATCH_SIZE):
        batch = results[offset : offset + PERSISTENCE_BATCH_SIZE]
        db.execute(
            insert(AnalysisResult),
            [_insert_values(AnalysisResult, item) for item in batch],
        )
    for offset in range(0, len(anomalies), PERSISTENCE_BATCH_SIZE):
        batch = anomalies[offset : offset + PERSISTENCE_BATCH_SIZE]
        inserted_ids = db.execute(
            insert(Anomaly).returning(
                Anomaly.id,
                Anomaly.detected_at,
                sort_by_parameter_order=True,
            ),
            [_insert_values(Anomaly, item) for item in batch],
        ).all()
        if len(inserted_ids) != len(batch):
            raise RuntimeError("Bulk anomaly insert returned an unexpected row count")
        for anomaly, (anomaly_id, detected_at) in zip(
            batch, inserted_ids, strict=True
        ):
            anomaly.id = anomaly_id
            anomaly.detected_at = detected_at


def _persist_detector_artifact(
    *,
    detector: Detector,
    model: object,
    analysis_id: int,
    parameters: dict,
) -> dict:
    artifact_dir = Path(settings.UPLOAD_DIR) / "analysis_models"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    filename = f"analysis-{analysis_id}-{detector.algorithm}-{uuid4().hex}.joblib"
    artifact_path = artifact_dir / filename
    artifact = detector.serialize(
        {
            "artifact_version": detector.version(),
            "configuration": parameters,
            "analysis_run_id": analysis_id,
            "pipeline": model,
        }
    )
    temporary_path = artifact_dir / f".{filename}.tmp"
    try:
        temporary_path.write_bytes(artifact)
        os.replace(temporary_path, artifact_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    relative_path = artifact_path.relative_to(Path(settings.UPLOAD_DIR))
    return {
        "path": str(relative_path),
        "version": detector.version(),
        "sha256": hashlib.sha256(artifact).hexdigest(),
        "configuration": parameters,
    }


def _robust_z_scores(matrix: np.ndarray) -> np.ndarray:
    centers = np.median(matrix, axis=0)
    deviations = np.median(np.abs(matrix - centers), axis=0) * 1.4826
    standard_deviations = np.std(matrix, axis=0)
    scales = np.where(deviations > 0, deviations, standard_deviations)
    scales = np.where(scales > 0, scales, 1.0)
    return np.abs((matrix - centers) / scales)


def _inclusive_score_percentiles(scores: np.ndarray) -> np.ndarray:
    values = np.asarray(scores, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("Anomaly scores must be a one-dimensional array")
    if not len(values):
        return np.empty(0, dtype=np.float64)
    percentiles = np.searchsorted(np.sort(values), values, side="right") / len(values)
    return np.where(np.isnan(values), 0.0, percentiles)


def _multiple_testing_adjusted_threshold(
    threshold: float, feature_count: int
) -> float:
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("Z-score threshold must be a positive finite number")
    if feature_count < 1:
        raise ValueError("At least one feature is required for Z-score analysis")
    if feature_count == 1:
        return threshold
    adjusted_tail_probability = float(norm.sf(threshold)) / feature_count
    if adjusted_tail_probability == 0:
        raise ValueError("Z-score threshold is too large for numerical adjustment")
    return float(norm.isf(adjusted_tail_probability))


def _score_z_score(
    matrix: np.ndarray,
    *,
    threshold: float,
    missing_values_strategy: str,
) -> tuple[np.ndarray, np.ndarray, float]:
    effective_threshold = _multiple_testing_adjusted_threshold(
        threshold, matrix.shape[1]
    )
    detector = ZScoreDetector()
    model = detector.fit(
        matrix,
        parameters={"threshold": effective_threshold},
        missing_values_strategy=missing_values_strategy,
        scaling="none",
    )
    return (
        detector.score(model, matrix),
        detector.predict(model, matrix),
        effective_threshold,
    )


def run_analysis(analysis_id: int) -> None:
    db = SessionLocal()
    started = time.perf_counter()
    created_artifact_path: Path | None = None
    analysis = db.get(AnalysisRun, analysis_id)
    if analysis is None:
        logger.error("Cannot run analysis %s because it does not exist", analysis_id)
        db.close()
        return
    db.execute(delete(Anomaly).where(Anomaly.analysis_run_id == analysis.id))
    db.execute(delete(AnalysisResult).where(AnalysisResult.analysis_run_id == analysis.id))
    analysis.status = "running"
    analysis.started_at = datetime.now(timezone.utc)
    analysis.completed_at = None
    analysis.error_message = None
    db.commit()
    try:
        dataset = db.get(Dataset, analysis.dataset_id)
        model_config = db.get(ModelConfig, analysis.model_config_id)
        if dataset is None or model_config is None:
            raise ValueError("The dataset or model configuration no longer exists")
        run_configuration = analysis.metrics.get("configuration", {})
        source_start_row = analysis.source_start_row
        source_end_row = analysis.source_end_row
        is_nasa_ims = dataset.file_format == "nasa_ims"
        source_row_offset = 1 if is_nasa_ims else 2
        if is_nasa_ims:
            records_query = select(MeasurementRecord).where(
                MeasurementRecord.dataset_id == dataset.id
            )
            if source_start_row is not None:
                records_query = records_query.where(
                    MeasurementRecord.source_row_number >= source_start_row
                )
            if source_end_row is not None:
                records_query = records_query.where(
                    MeasurementRecord.source_row_number <= source_end_row
                )
            records = list(
                db.scalars(
                    records_query.order_by(MeasurementRecord.source_row_number)
                )
            )
            frame = pd.DataFrame(
                [record.values for record in records],
                index=[
                    record.source_row_number - source_row_offset
                    for record in records
                ],
            )
        else:
            records = []
            frame = pd.read_csv(dataset.file_path)
            if source_start_row is not None:
                frame = frame.loc[frame.index + source_row_offset >= source_start_row]
            if source_end_row is not None:
                frame = frame.loc[frame.index + source_row_offset <= source_end_row]
        numeric_columns = [
            column
            for column in frame.columns
            if pd.api.types.is_numeric_dtype(frame[column])
            and is_analysis_feature(column, dataset.file_format)
        ]
        configured_features = (
            run_configuration.get("features")
            or model_config.parameters.get("features", [])
        )
        feature_names = configured_features or numeric_columns
        missing_features = [name for name in feature_names if name not in numeric_columns]
        if missing_features:
            raise ValueError(
                f"Selected features are missing, non-numeric, or metadata-only: {missing_features}"
            )
        if not feature_names:
            raise ValueError("The dataset has no numeric features available for analysis")
        feature_frame = frame[feature_names].apply(pd.to_numeric, errors="coerce")
        empty_features = feature_frame.columns[feature_frame.isna().all()].tolist()
        if empty_features:
            raise ValueError(f"Selected features contain no numeric values: {empty_features}")
        valid_rows = feature_frame.notna().any(axis=1)
        feature_frame = feature_frame.loc[valid_rows]
        if len(feature_frame) < 2:
            raise ValueError("At least two measurements with numeric values are required")
        strategy = run_configuration.get(
            "missing_values_strategy",
            model_config.parameters.get("missing_values_strategy", "median"),
        )
        if strategy not in {"drop", "mean", "median"}:
            raise ValueError("Missing-value strategy must be drop, mean, or median")
        scaling = run_configuration.get(
            "scaling", model_config.parameters.get("scaling", "standard")
        )
        if scaling not in {"standard", "none"}:
            raise ValueError("Scaling must be standard or none")
        algorithm = normalize_algorithm_name(model_config.algorithm)
        params = model_config.parameters
        walk_forward = algorithm in DETECTOR_REGISTRY and DETECTOR_REGISTRY[
            algorithm
        ].uses_walk_forward
        from sklearn import __version__ as sklearn_version
        from sklearn.ensemble import IsolationForest
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler
        if walk_forward:
            matrix = feature_frame.to_numpy(dtype=float)
            source_indexes = feature_frame.index
            raw_matrix = matrix.copy()
            feature_z_scores = np.empty((0, len(feature_names)), dtype=float)
        else:
            if strategy == "drop":
                kept = feature_frame.dropna()
                source_indexes = kept.index
                matrix = kept.to_numpy(dtype=float)
            else:
                source_indexes = feature_frame.index
                matrix = SimpleImputer(strategy=strategy).fit_transform(feature_frame)
            if len(matrix) < 2:
                raise ValueError("At least two complete measurements are required")
            raw_matrix = matrix.copy()
            feature_z_scores = _robust_z_scores(raw_matrix)
            if scaling == "standard":
                matrix = StandardScaler().fit_transform(matrix)

        walk_forward_result = None
        artifact_metadata = None
        pretrained_metadata = None
        if walk_forward:
            detector = get_detector(algorithm)
            validated_config = detector.validate_config(params)
            if algorithm == "ensemble":
                validated_config = resolve_ensemble_features(
                    validated_config, list(feature_names)
                )
            walk_forward_result = walk_forward_predict(
                detector,
                raw_matrix,
                parameters=validated_config,
                missing_values_strategy=strategy,
                scaling=scaling,
            )
            eligible_positions = (
                np.flatnonzero(np.isfinite(raw_matrix).all(axis=1))
                if strategy == "drop"
                else np.flatnonzero(np.isfinite(raw_matrix).any(axis=1))
            )
            scored_positions = walk_forward_result.positions
            source_indexes = feature_frame.index[scored_positions]
            predictions = walk_forward_result.predictions
            scores = walk_forward_result.scores
            score_thresholds = walk_forward_result.score_thresholds
            feature_z_scores = walk_forward_result.feature_deviations
            score_threshold = None
            pretrained_metadata = None
            if walk_forward_result.final_model is not None:
                artifact_metadata = _persist_detector_artifact(
                    detector=detector,
                    model=walk_forward_result.final_model,
                    analysis_id=analysis.id,
                    parameters={
                        "algorithm": algorithm,
                        "algorithm_version": detector.version(),
                        "detector_parameters": validated_config,
                        "features": feature_names,
                        "missing_values_strategy": strategy,
                        "scaling": scaling,
                        "minimum_history": walk_forward_result.minimum_history,
                        "batch_size": 256,
                    },
                )
                created_artifact_path = Path(settings.UPLOAD_DIR) / artifact_metadata["path"]
        elif algorithm in {"isolation_forest", "isolation forest"}:
            contamination = model_config.contamination or params.get("contamination", "auto")
            if contamination != "auto" and not 0 < float(contamination) <= 0.5:
                raise ValueError("Contamination must be between 0 and 0.5, or auto")
            score_threshold = None
            pretrained_metadata = None
            if (
                is_compatible_nasa_ims_schema(dataset.file_format, feature_names)
                and model_is_available()
            ):
                model_matrix = feature_frame.loc[source_indexes].to_numpy(dtype=float)
                predictions, scores, score_threshold, pretrained_metadata = (
                    predict_nasa_ims_matrix(model_matrix)
                )
            else:
                estimator = IsolationForest(
                    n_estimators=int(params.get("n_estimators", 100)),
                    contamination=contamination,
                    max_samples=params.get("max_samples", "auto"),
                    max_features=float(params.get("max_features", 1.0)),
                    bootstrap=bool(params.get("bootstrap", False)),
                    random_state=int(params.get("random_state", 42)),
                    n_jobs=int(params.get("n_jobs", -1)),
                )
                predictions = estimator.fit_predict(matrix)
                scores = -estimator.score_samples(matrix)
                score_threshold = float(-estimator.offset_)
            score_thresholds = np.full(len(scores), score_threshold, dtype=float)
        elif algorithm in {"z_score", "z-score"}:
            threshold = float(params.get("threshold", 3.0))
            scores, predictions, score_threshold = _score_z_score(
                raw_matrix,
                threshold=threshold,
                missing_values_strategy=strategy,
            )
            score_thresholds = np.full(len(scores), score_threshold, dtype=float)
        else:
            raise ValueError(f"Unsupported analysis algorithm: {model_config.algorithm}")

        records_by_source_row = {
            record.source_row_number - source_row_offset: record for record in records
        }
        source_row_positions = {
            int(source_index): position
            for position, source_index in enumerate(feature_frame.index)
        }
        source_values_matrix = feature_frame.to_numpy(dtype=float)

        def feature_values_for(source_index: int) -> dict[str, float | None]:
            row = source_values_matrix[source_row_positions[int(source_index)]]
            return {
                name: float(value) if np.isfinite(value) else None
                for name, value in zip(feature_names, row, strict=True)
            }

        anomalies = []
        results = []
        if walk_forward_result is not None:
            scored_position_set = set(int(position) for position in walk_forward_result.positions)
            eligible_position_set = set(int(position) for position in eligible_positions)
            for position, source_index in enumerate(feature_frame.index):
                if position in scored_position_set:
                    continue
                history_size = int(np.count_nonzero(eligible_positions < position))
                is_incomplete = (
                    strategy == "drop" and position not in eligible_position_set
                )
                values = feature_values_for(source_index)
                source_record = records_by_source_row.get(int(source_index))
                results.append(
                    AnalysisResult(
                        analysis_run_id=analysis.id,
                        measurement_record_id=(
                            source_record.id if source_record else None
                        ),
                        source_row_number=int(source_index) + source_row_offset,
                        is_anomaly=False,
                        anomaly_score=None,
                        history_state=(
                            "excluded_missing_values"
                            if is_incomplete
                            else "insufficient_history"
                        ),
                        history_size=history_size,
                        history_required=walk_forward_result.minimum_history,
                        result_details={},
                        feature_values=values,
                    )
                )
        score_percentiles = (
            _inclusive_score_percentiles(scores)
            if algorithm not in {"z_score", "z-score"} and walk_forward_result is None
            else None
        )
        z_score_severity_thresholds = (
            {
                level: _multiple_testing_adjusted_threshold(
                    float(params.get("threshold", 3.0)) + offset,
                    len(feature_names),
                )
                for level, offset in (
                    ("low", 0),
                    ("medium", 1),
                    ("high", 2),
                    ("critical", 3),
                )
            }
            if algorithm in {"z_score", "z-score"}
            else None
        )
        for row_index, (source_index, prediction, score) in enumerate(zip(
            source_indexes, predictions, scores, strict=True
        )):
            values = feature_values_for(source_index)
            source_record = records_by_source_row.get(int(source_index))
            result_details = (
                dict(walk_forward_result.result_details[row_index])
                if walk_forward_result is not None
                else {}
            )
            shift_start_position = result_details.get("shift_started_position")
            if shift_start_position is not None:
                start_index = feature_frame.index[int(shift_start_position)]
                start_source_row = int(start_index) + source_row_offset
                start_record = records_by_source_row.get(int(start_index))
                time_column = run_configuration.get("time_column")
                start_time = (
                    start_record.timestamp.isoformat()
                    if start_record is not None and start_record.timestamp is not None
                    else None
                )
                if (
                    start_time is None
                    and isinstance(time_column, str)
                    and time_column in frame.columns
                ):
                    raw_time = frame.loc[start_index, time_column]
                    if pd.notna(raw_time):
                        start_time = str(raw_time)
                result_details["shift_started_source_row_number"] = start_source_row
                result_details["shift_started_at"] = start_time
            else:
                result_details["shift_started_source_row_number"] = None
                result_details["shift_started_at"] = None
            is_anomaly = bool(prediction == -1)
            results.append(
                AnalysisResult(
                    analysis_run_id=analysis.id,
                    measurement_record_id=source_record.id if source_record else None,
                    source_row_number=int(source_index) + source_row_offset,
                    is_anomaly=is_anomaly,
                    anomaly_score=float(score),
                    history_state=(
                        result_details.get("ensemble_state", "sufficient")
                        if algorithm == "ensemble"
                        else "sufficient"
                        if walk_forward_result is not None
                        else None
                    ),
                    history_size=(
                        int(walk_forward_result.history_sizes[row_index])
                        if walk_forward_result is not None
                        else None
                    ),
                    history_required=(
                        walk_forward_result.minimum_history
                        if walk_forward_result is not None
                        else None
                    ),
                    result_details=result_details,
                    feature_values=values,
                )
            )
            if not is_anomaly:
                continue
            strongest_features = sorted(
                zip(feature_names, feature_z_scores[row_index], strict=True),
                key=lambda item: float(item[1]),
                reverse=True,
            )[:3]
            strongest_feature, strongest_z_score = strongest_features[0]
            feature_evidence = ", ".join(
                f"{name} ({float(z_score):.2f} standard deviations)"
                for name, z_score in strongest_features
            )
            if algorithm == "z_score":
                if z_score_severity_thresholds is None:
                    raise RuntimeError("Z-score severity thresholds were not computed")
                severity_thresholds = z_score_severity_thresholds
                severity = next(
                    level
                    for level in ("critical", "high", "medium", "low")
                    if float(score) >= severity_thresholds[level]
                )
                explanation = (
                    f"Maximum robust Z-score {float(score):.2f} exceeded the "
                    f"multiple-testing-adjusted threshold {score_threshold:.2f} "
                    f"(configured per-feature threshold "
                    f"{float(params.get('threshold', 3.0)):.2f} across "
                    f"{len(feature_names)} features); strongest feature "
                    f"deviations: {feature_evidence}."
                )
            elif algorithm == "ensemble":
                agreement_count = int(result_details.get("agreement_count", 0))
                voted_count = int(result_details.get("voted_count", 0))
                confidence = float(result_details.get("confidence", 0.0))
                high_confidence = bool(result_details.get("high_confidence", False))
                severity_thresholds = {
                    "confidence": confidence,
                    "high_confidence_threshold": float(
                        result_details["configuration"]["high_confidence_threshold"]
                    ),
                    "rule": result_details.get("confidence_rule"),
                }
                severity = "high" if high_confidence else "medium"
                explanation = (
                    f"The detector ensemble flagged this measurement with "
                    f"{agreement_count} of {voted_count} eligible detectors agreeing "
                    f"(weighted confidence {confidence:.3f}). This is statistical "
                    "evidence for human review, not a physical-failure diagnosis."
                )
            elif walk_forward_result is not None:
                row_threshold = float(score_thresholds[row_index])
                high_threshold, critical_threshold = (
                    walk_forward_result.severity_thresholds[row_index]
                )
                severity_thresholds = {
                    "decision_threshold": row_threshold,
                    "high_empirical_score_threshold": float(high_threshold),
                    "critical_empirical_score_threshold": float(critical_threshold),
                    "reference": "earlier fitted history 95th and 99.5th percentiles",
                }
                severity = (
                    "critical"
                    if score >= critical_threshold
                    else "high"
                    if score >= high_threshold
                    else "medium"
                )
                explanation = (
                    f"{model_config.algorithm} flagged this reading "
                    f"(outlier score {float(score):.4f}, decision threshold "
                    f"{row_threshold:.4f}, earlier history "
                    f"{int(walk_forward_result.history_sizes[row_index])} readings). "
                    f"Severity uses the earlier-history score percentiles "
                    f"(high {float(high_threshold):.4f}, critical "
                    f"{float(critical_threshold):.4f}). This is statistical "
                    "evidence for human review."
                )
                shift_direction = result_details.get("shift_direction")
                shift_started_at = result_details.get("shift_started_at")
                if shift_direction:
                    explanation += (
                        f" The control chart indicates a {shift_direction} shift"
                        + (
                            f" starting at {shift_started_at}"
                            if shift_started_at
                            else f" at source row {result_details.get('shift_started_source_row_number')}"
                        )
                        + "."
                    )
            else:
                if score_percentiles is None:
                    raise RuntimeError("Isolation Forest score percentiles were not computed")
                score_percentile = float(score_percentiles[row_index])
                severity_thresholds = {
                    "high_percentile": 0.95,
                    "critical_percentile": 0.995,
                }
                severity = (
                    "critical"
                    if score_percentile >= severity_thresholds["critical_percentile"]
                    else "high"
                    if score_percentile >= severity_thresholds["high_percentile"]
                    else "medium"
                )
                explanation = (
                    f"Isolation Forest classified this measurement as an outlier "
                    f"(outlier score {float(score):.4f}, score percentile "
                    f"{score_percentile * 100:.1f}); largest feature deviation: "
                    f"{strongest_feature} ({float(strongest_z_score):.2f} "
                    "standard deviations)."
                )
            anomalies.append(
                Anomaly(
                    analysis_run_id=analysis.id,
                    measurement_record_id=source_record.id if source_record else None,
                    anomaly_type=model_config.algorithm,
                    anomaly_score=float(score),
                    severity=severity,
                    status="new",
                    feature_values=values,
                    expected_range={
                        "score_threshold": float(score_thresholds[row_index]),
                        "configured_threshold": (
                            float(params.get("threshold", 3.0))
                            if algorithm in {"z_score", "z-score"}
                            else None
                        ),
                        "feature_count": len(feature_names),
                        "baseline_method": (
                            "median/MAD with standard-deviation fallback"
                            if algorithm in {"z_score", "z-score"}
                            else None
                        ),
                        "multiple_testing_correction": (
                            "Bonferroni"
                            if algorithm in {"z_score", "z-score"}
                            else None
                        ),
                        "severity_thresholds": (
                            severity_thresholds
                            if algorithm == "z_score"
                            or walk_forward_result is not None
                            else {
                                "high_percentile": severity_thresholds["high_percentile"],
                                "critical_percentile": severity_thresholds["critical_percentile"],
                            }
                        ),
                        "strongest_feature": strongest_feature,
                        "strongest_feature_z_score": float(strongest_z_score),
                        "detector_details": result_details,
                        "top_feature_deviations": [
                            {"feature": name, "absolute_z_score": float(z_score)}
                            for name, z_score in strongest_features
                        ],
                    },
                    description=(
                        explanation
                        + " This is a statistical flag for human review, not a fault diagnosis."
                    ),
                )
            )
        _bulk_persist_analysis_rows(db, results, anomalies)
        analysis.status = "completed"
        analysis.total_records = (
            int(len(eligible_positions))
            if walk_forward_result is not None
            else len(matrix)
        )
        analysis.anomalies_found = len(anomalies)
        analysis.metrics = {
            "configuration": analysis.metrics.get("configuration", {}),
            "algorithm": model_config.algorithm,
            "algorithm_version": (
                detector.version()
                if walk_forward_result is not None
                else sklearn_version
                if algorithm == "isolation_forest"
                else "1"
            ),
            "features": feature_names,
            "scoring": (
                {
                    "method": algorithm,
                    "decision_threshold": (
                        float(score_thresholds[0])
                        if len(score_thresholds)
                        else None
                    ),
                    "minimum_history": walk_forward_result.minimum_history,
                    "fit_scope": "earlier readings only; bounded chronological batches",
                    "batch_size": 256,
                    "wording": "statistical evidence for human review",
                }
                if walk_forward_result is not None
                else
                {
                    "method": "robust_z_score",
                    "baseline": "per-feature median and scaled MAD",
                    "zero_mad_fallback": "per-feature standard deviation",
                    "configured_per_feature_threshold": float(
                        params.get("threshold", 3.0)
                    ),
                    "effective_threshold": score_threshold,
                    "multiple_testing_correction": "Bonferroni",
                    "severity_sigma_offsets": {
                        "low": 0,
                        "medium": 1,
                        "high": 2,
                        "critical": 3,
                    },
                }
                if algorithm in {"z_score", "z-score"}
                else {
                    "method": "isolation_forest",
                    "score_threshold": score_threshold,
                    "severity_score_percentiles": {
                        "high": 0.95,
                        "critical": 0.995,
                    },
                }
            ),
            "pretrained_nasa_ims_model": pretrained_metadata,
            "detector_artifact": artifact_metadata,
            "history_evaluation": (
                {
                    "state": (
                        "sufficient"
                        if len(walk_forward_result.positions) > 0
                        else "insufficient_history"
                    ),
                    "available": int(len(eligible_positions)),
                    "required": walk_forward_result.minimum_history,
                    "scored_rows": int(len(walk_forward_result.positions)),
                    "insufficient_history_rows": int(
                        sum(
                            result.history_state == "insufficient_history"
                            for result in results
                        )
                    ),
                    "excluded_missing_value_rows": int(
                        sum(
                            result.history_state == "excluded_missing_values"
                            for result in results
                        )
                    ),
                }
                if walk_forward_result is not None
                else None
            ),
            "dataset_format": dataset.file_format,
            "execution_seconds": round(time.perf_counter() - started, 4),
            "excluded_rows": int(
                len(frame) - len(eligible_positions)
                if walk_forward_result is not None
                else len(frame) - len(matrix)
            ),
            "labeled_rows": sum(record.is_labeled_anomaly is not None for record in records),
        }
        labels_by_source_row = {
            record.source_row_number - source_row_offset: record.is_labeled_anomaly
            for record in records
            if record.is_labeled_anomaly is not None
        }
        labeled_predictions = [
            (labels_by_source_row[int(source_index)], bool(prediction == -1))
            for source_index, prediction in zip(source_indexes, predictions, strict=True)
            if int(source_index) in labels_by_source_row
        ]
        if labeled_predictions:
            true_positive = sum(actual and predicted for actual, predicted in labeled_predictions)
            false_positive = sum(not actual and predicted for actual, predicted in labeled_predictions)
            false_negative = sum(actual and not predicted for actual, predicted in labeled_predictions)
            precision = (
                true_positive / (true_positive + false_positive)
                if true_positive + false_positive
                else 0.0
            )
            recall = (
                true_positive / (true_positive + false_negative)
                if true_positive + false_negative
                else 0.0
            )
            analysis.metrics["evaluation"] = {
                "labeled_measurements": len(labeled_predictions),
                "precision": precision,
                "recall": recall,
                "f1_score": (
                    2 * precision * recall / (precision + recall)
                    if precision + recall
                    else 0.0
                ),
                "confusion_matrix": {
                    "true_positive": true_positive,
                    "false_positive": false_positive,
                    "false_negative": false_negative,
                    "true_negative": sum(
                        not actual and not predicted
                        for actual, predicted in labeled_predictions
                    ),
                },
            }
        analysis.completed_at = datetime.now(timezone.utc)
        measurement_by_id = {record.id: record for record in records}
        device = dataset.device
        if analysis_notifications_enabled(run_configuration):
            active_recipients = list(
                db.scalars(
                    select(User.id).where(
                        User.is_active.is_(True),
                        User.role.in_(
                            {
                                "administrator",
                                "data_analyst",
                                "maintenance_technician",
                            }
                        ),
                    )
                )
            )
            recipient_ids = list(
                dict.fromkeys(
                    (
                        [analysis.started_by]
                        if analysis.started_by is not None
                        else []
                    )
                    + active_recipients
                )
            )
        else:
            recipient_ids = []
        if analysis_notifications_enabled(run_configuration):
            summary_notifications = [
                    {
                        "user_id": recipient_id,
                        "title": "Analysis completed",
                        "message": (
                            f"Analysis #{analysis.id} completed with "
                            f"{analysis.anomalies_found} anomalies."
                        ),
                        "notification_type": "analysis_completed",
                        "related_entity_type": "analysis",
                        "related_entity_id": analysis.id,
                        "details": {
                            "analysis": {
                                "run_id": analysis.id,
                                "dataset_id": dataset.id,
                                "dataset_name": dataset.name,
                                "algorithm": model_config.algorithm,
                                "anomaly_count": analysis.anomalies_found,
                            },
                            "filtered_anomaly_list": (
                                f"/anomalies?analysis_run_id={analysis.id}"
                            ),
                            "source_type": "dataset",
                        },
                        "dedupe_key": f"analysis-summary:{analysis.id}:{recipient_id}",
                        "is_read": False,
                    }
                    for recipient_id in recipient_ids
            ]
            notify_users_batch(db, summary_notifications)
        db.commit()
    except Exception as exc:
        logger.exception("Analysis %s failed", analysis_id)
        db.rollback()
        if created_artifact_path is not None:
            try:
                created_artifact_path.unlink(missing_ok=True)
            except OSError:
                logger.exception(
                    "Could not remove incomplete detector artifact %s",
                    created_artifact_path,
                )
        failed_analysis = db.get(AnalysisRun, analysis_id)
        if failed_analysis is not None:
            failed_analysis.status = "failed"
            failed_analysis.error_message = str(exc)[:2000]
            failed_analysis.completed_at = datetime.now(timezone.utc)
            failed_configuration = failed_analysis.metrics.get("configuration", {})
            if analysis_notifications_enabled(failed_configuration):
                notify_user(
                    db,
                    failed_analysis.started_by,
                    "Analysis failed",
                    f"Analysis #{failed_analysis.id} failed. Review the analysis history for details.",
                    "analysis_failed",
                    "analysis",
                    failed_analysis.id,
                )
            db.commit()
    finally:
        db.close()