import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.datasets.application.nasa_ims import CHANNEL_FEATURES
from app.shared.models import Dataset, MeasurementRecord

MODEL_NAME = "NASA IMS Bearings Isolation Forest"
MODEL_VERSION = 1
TRAIN_DATASET_SUFFIX = "2nd_test"
TEST_DATASET_SUFFIX = "3rd_test"
MAX_EVALUATION_ROWS = 50_000
FEATURE_NAMES = tuple(
    f"bearing_{bearing}_{feature}"
    for bearing in range(1, 5)
    for feature in CHANNEL_FEATURES
)


def model_artifact_path() -> Path:
    return (
        Path(settings.UPLOAD_DIR).resolve()
        / "models"
        / "nasa_ims_bearing_isolation_forest.joblib"
    )


def is_compatible_nasa_ims_schema(
    file_format: str, feature_names: Sequence[str]
) -> bool:
    return file_format == "nasa_ims" and tuple(feature_names) == FEATURE_NAMES


def is_compatible_nasa_ims_observation(
    features: Mapping[str, float],
) -> bool:
    if set(features) != set(FEATURE_NAMES):
        return False
    try:
        return all(math.isfinite(float(features[name])) for name in FEATURE_NAMES)
    except (TypeError, ValueError, OverflowError):
        return False


def model_is_available() -> bool:
    return model_artifact_path().is_file()


def load_nasa_ims_model() -> dict[str, Any]:
    path = model_artifact_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"NASA IMS Isolation Forest model is not trained: {path}"
        )
    artifact = joblib.load(path)
    if artifact.get("model_version") != MODEL_VERSION:
        raise ValueError("Unsupported NASA IMS model artifact version")
    if tuple(artifact.get("features", ())) != FEATURE_NAMES:
        raise ValueError("NASA IMS model artifact has an incompatible feature schema")
    return artifact


def predict_nasa_ims_matrix(
    matrix: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float, dict[str, Any]]:
    artifact = load_nasa_ims_model()
    pipeline = artifact["pipeline"]
    transformed = pipeline[:-1].transform(matrix)
    estimator: IsolationForest = pipeline.steps[-1][1]
    predictions = estimator.predict(transformed)
    scores = -estimator.score_samples(transformed)
    return (
        predictions,
        scores,
        float(-estimator.offset_),
        artifact["metadata"],
    )


def predict_nasa_ims_observation(
    features: Mapping[str, float],
) -> dict[str, float | bool]:
    missing_features = [name for name in FEATURE_NAMES if name not in features]
    if missing_features:
        raise ValueError(
            "NASA IMS prediction requires all compatible bearing features; "
            f"missing {missing_features}"
        )
    matrix = np.asarray(
        [[features[name] for name in FEATURE_NAMES]], dtype=float
    )
    predictions, scores, threshold, _ = predict_nasa_ims_matrix(matrix)
    return {
        "is_anomaly": bool(predictions[0] == -1),
        "score": float(scores[0]),
        "score_threshold": threshold,
    }


def _find_dataset(
    db: Session, suffix: str, expected_features: Sequence[str]
) -> Dataset:
    datasets = list(
        db.scalars(
            select(Dataset)
            .where(
                Dataset.file_format == "nasa_ims",
                Dataset.status != "archived",
            )
            .order_by(Dataset.id)
        )
    )
    for dataset in datasets:
        if not dataset.name.casefold().endswith(suffix):
            continue
        available = {
            column.get("name")
            for column in dataset.column_metadata
            if column.get("type") == "numeric"
        }
        if set(expected_features).issubset(available):
            return dataset
    raise ValueError(
        f"An imported NASA IMS {suffix} dataset with the compatible "
        "four-bearing feature schema is required."
    )


def _dataset_matrix(
    db: Session,
    dataset: Dataset,
    *,
    max_rows: int | None = None,
) -> tuple[np.ndarray, int, bool]:
    stride = (
        max(1, math.ceil(dataset.row_count / max_rows))
        if max_rows is not None
        else 1
    )
    statement = (
        select(MeasurementRecord.values, MeasurementRecord.is_labeled_anomaly)
        .where(
            MeasurementRecord.dataset_id == dataset.id,
            MeasurementRecord.source_row_number % stride == 0,
        )
        .order_by(MeasurementRecord.source_row_number)
    )
    matrix_rows: list[list[float]] = []
    any_ground_truth_labels = False
    for values, label in db.execute(statement):
        row = []
        for name in FEATURE_NAMES:
            try:
                value = float(values[name])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    f"NASA IMS record is missing numeric feature {name!r}."
                ) from error
            row.append(value if math.isfinite(value) else float("nan"))
        matrix_rows.append(row)
        any_ground_truth_labels |= label is not None

    if len(matrix_rows) < 2:
        raise ValueError(
            f"NASA IMS dataset {dataset.name!r} has fewer than two compatible "
            "records for this operation."
        )
    matrix = np.asarray(matrix_rows, dtype=np.float64)
    if np.isnan(matrix).all(axis=0).any():
        raise ValueError("A NASA IMS training feature contains no finite values.")
    return matrix, len(matrix), any_ground_truth_labels


def _training_pipeline(training_rows: int) -> Any:
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        IsolationForest(
            n_estimators=200,
            contamination=0.05,
            max_samples=min(2048, training_rows),
            random_state=42,
            n_jobs=-1,
        ),
    )


def train_nasa_ims_model(db: Session) -> dict[str, Any]:
    training_dataset = _find_dataset(
        db, TRAIN_DATASET_SUFFIX, FEATURE_NAMES
    )
    test_dataset = _find_dataset(db, TEST_DATASET_SUFFIX, FEATURE_NAMES)
    training_matrix, training_rows, _ = _dataset_matrix(db, training_dataset)
    test_matrix, test_rows, has_ground_truth_labels = _dataset_matrix(
        db, test_dataset, max_rows=MAX_EVALUATION_ROWS
    )

    pipeline = _training_pipeline(training_rows)
    pipeline.fit(training_matrix)
    estimator: IsolationForest = pipeline.steps[-1][1]
    test_transformed = pipeline[:-1].transform(test_matrix)
    test_predictions = estimator.predict(test_transformed)
    test_scores = -estimator.score_samples(test_transformed)
    anomaly_count = int(np.count_nonzero(test_predictions == -1))
    metadata = {
        "name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_dataset_id": training_dataset.id,
        "training_dataset_name": training_dataset.name,
        "training_rows": training_rows,
        "test_dataset_id": test_dataset.id,
        "test_dataset_name": test_dataset.name,
        "test_rows_evaluated": test_rows,
        "test_rows_available": test_dataset.row_count,
        "test_anomaly_predictions": anomaly_count,
        "test_anomaly_rate": anomaly_count / test_rows,
        "test_score_mean": float(np.mean(test_scores)),
        "test_score_std": float(np.std(test_scores)),
        "test_score_quantiles": {
            str(percentile): float(np.quantile(test_scores, percentile / 100))
            for percentile in (50, 90, 95, 99)
        },
        "ground_truth_labels_available": has_ground_truth_labels,
        "evaluation_note": (
            "Descriptive holdout scoring only; reliable ground-truth labels "
            "are unavailable, so accuracy metrics are not claimed."
        ),
        "features": list(FEATURE_NAMES),
        "preprocessing": {
            "imputation": "median",
            "scaling": "standard",
            "feature_source": "NASA IMS 256-sample vibration windows",
            "sample_rate_hz": 20_000,
        },
        "isolation_forest": {
            "n_estimators": 200,
            "contamination": 0.05,
            "max_samples": min(2048, training_rows),
            "random_state": 42,
        },
        "scikit_learn_version": sklearn_version,
    }
    artifact = {
        "model_version": MODEL_VERSION,
        "features": list(FEATURE_NAMES),
        "metadata": metadata,
        "pipeline": pipeline,
    }
    path = model_artifact_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        joblib.dump(artifact, temporary_path, compress=3)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)

    loaded_artifact = load_nasa_ims_model()
    loaded_estimator: IsolationForest = loaded_artifact["pipeline"].steps[-1][1]
    loaded_preprocessor = loaded_artifact["pipeline"][:-1]
    loaded_predictions = loaded_estimator.predict(
        loaded_preprocessor.transform(test_matrix[: min(128, test_rows)])
    )
    if not np.array_equal(
        loaded_predictions, test_predictions[: len(loaded_predictions)]
    ):
        raise RuntimeError("Reloaded NASA IMS model predictions do not match training output")
    metadata["artifact_path"] = str(path)
    metadata["artifact_size_bytes"] = path.stat().st_size
    metadata["reload_prediction_check_rows"] = len(loaded_predictions)
    return metadata
