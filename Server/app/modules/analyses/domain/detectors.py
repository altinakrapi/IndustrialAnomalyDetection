from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO
from typing import Any, ClassVar

import joblib
import numpy as np
from numpy.typing import NDArray
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.neural_network import MLPRegressor
from sklearn.neighbors import LocalOutlierFactor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM

FloatMatrix = NDArray[np.float64]
PredictionVector = NDArray[np.int64]
ScoreVector = NDArray[np.float64]


class Detector(ABC):
    algorithm: ClassVar[str]
    display_name: ClassVar[str]
    artifact_version: ClassVar[int] = 1
    uses_walk_forward: ClassVar[bool] = False
    parameter_definitions: ClassVar[list[dict[str, Any]]] = []

    @classmethod
    @abstractmethod
    def validate_config(cls, parameters: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def minimum_history(cls, parameters: dict[str, Any]) -> int:
        raise NotImplementedError

    @classmethod
    def version(cls) -> str:
        return f"{cls.algorithm}-v{cls.artifact_version}-sklearn-{sklearn_version}"

    @abstractmethod
    def fit(
        self,
        values: FloatMatrix,
        *,
        parameters: dict[str, Any],
        missing_values_strategy: str,
        scaling: str,
    ) -> Any:
        raise NotImplementedError

    @abstractmethod
    def score(self, model: Any, values: FloatMatrix) -> ScoreVector:
        raise NotImplementedError

    @abstractmethod
    def predict(self, model: Any, values: FloatMatrix) -> PredictionVector:
        raise NotImplementedError

    def decision_threshold(self, model: Any) -> float:
        if isinstance(model, Pipeline):
            estimator = model.named_steps["detector"]
            return (
                -float(np.asarray(estimator.offset_).item())
                if hasattr(estimator, "offset_")
                else 0.0
            )
        if isinstance(model, dict):
            return float(model.get("threshold", 0.0))
        return 0.0

    def feature_deviations(
        self, model: Any, history: FloatMatrix, values: FloatMatrix
    ) -> FloatMatrix:
        preprocessing = (
            model[:-1]
            if isinstance(model, Pipeline)
            else model.get("preprocessing")
            if isinstance(model, dict)
            else getattr(model, "preprocessing", None)
        )
        if preprocessing is None:
            return np.abs(np.asarray(values, dtype=np.float64))
        transformed_history = preprocessing.transform(history)
        transformed_values = preprocessing.transform(values)
        centers = np.median(transformed_history, axis=0)
        scales = np.median(np.abs(transformed_history - centers), axis=0) * 1.4826
        scales = np.where(scales > 0, scales, np.std(transformed_history, axis=0))
        scales = np.where(scales > 0, scales, 1.0)
        return np.abs((transformed_values - centers) / scales)

    def history_score_reference(self, model: Any) -> ScoreVector | None:
        if isinstance(model, dict) and "training_scores" in model:
            return np.asarray(model["training_scores"], dtype=np.float64)
        if hasattr(model, "training_scores"):
            return np.asarray(model.training_scores, dtype=np.float64)
        return None

    def result_details(
        self, model: Any, values: FloatMatrix
    ) -> list[dict[str, Any]]:
        return [{} for _ in range(len(values))]

    @staticmethod
    def serialize(model: Any) -> bytes:
        buffer = BytesIO()
        joblib.dump(model, buffer)
        return buffer.getvalue()

    @staticmethod
    def deserialize(artifact: bytes) -> Any:
        return joblib.load(BytesIO(artifact))

    @staticmethod
    def _preprocessing_steps(
        missing_values_strategy: str,
        scaling: str,
    ) -> list[tuple[str, Any]]:
        if missing_values_strategy not in {"drop", "mean", "median"}:
            raise ValueError("Missing-value strategy must be drop, mean, or median")
        if scaling not in {"standard", "none"}:
            raise ValueError("Scaling must be standard or none")
        imputer = (
            "passthrough"
            if missing_values_strategy == "drop"
            else SimpleImputer(strategy=missing_values_strategy)
        )
        scaler = StandardScaler() if scaling == "standard" else "passthrough"
        return [("imputer", imputer), ("scaler", scaler)]

    @classmethod
    def catalog_entry(cls) -> dict[str, Any]:
        defaults = cls.validate_config({})
        return {
            "algorithm": cls.algorithm,
            "display_name": cls.display_name,
            "version": cls.version(),
            "minimum_history": cls.minimum_history(defaults),
            "execution_mode": "walk_forward" if cls.uses_walk_forward else "batch",
            "parameters": cls.parameter_definitions,
        }


@dataclass(slots=True)
class WalkForwardResult:
    positions: NDArray[np.int64]
    predictions: PredictionVector
    scores: ScoreVector
    score_thresholds: ScoreVector
    severity_thresholds: FloatMatrix
    history_sizes: NDArray[np.int64]
    feature_deviations: FloatMatrix
    result_details: list[dict[str, Any]]
    final_model: Any | None
    minimum_history: int


class IsolationForestDetector(Detector):
    algorithm = "isolation_forest"
    display_name = "Isolation Forest"
    parameter_definitions = [
        {"name": "n_estimators", "type": "integer", "default": 100, "minimum": 10, "maximum": 1000},
        {"name": "contamination", "type": "number_or_auto", "default": "auto", "minimum": 0.001, "maximum": 0.5},
        {"name": "random_state", "type": "integer", "default": 42, "minimum": 0, "maximum": 2147483647},
    ]

    @classmethod
    def validate_config(cls, parameters: dict[str, Any]) -> dict[str, Any]:
        n_estimators = parameters.get("n_estimators", 100)
        contamination = parameters.get("contamination", "auto")
        random_state = parameters.get("random_state", 42)
        if isinstance(n_estimators, bool) or not isinstance(n_estimators, int) or not 10 <= n_estimators <= 1000:
            raise ValueError("n_estimators must be an integer between 10 and 1000")
        if contamination != "auto" and (
            isinstance(contamination, bool)
            or not isinstance(contamination, (int, float))
            or not 0 < float(contamination) <= 0.5
        ):
            raise ValueError("contamination must be auto or a number in (0, 0.5]")
        if isinstance(random_state, bool) or not isinstance(random_state, int) or random_state < 0:
            raise ValueError("random_state must be a non-negative integer")
        max_samples = parameters.get("max_samples", "auto")
        if max_samples != "auto" and (
            isinstance(max_samples, bool)
            or not isinstance(max_samples, int)
            or max_samples < 1
        ):
            raise ValueError("max_samples must be auto or a positive integer")
        max_features = parameters.get("max_features", 1.0)
        if (
            isinstance(max_features, bool)
            or not isinstance(max_features, (int, float))
            or not np.isfinite(max_features)
            or not 0 < max_features <= 1
        ):
            raise ValueError("max_features must be a number in (0, 1]")
        bootstrap = parameters.get("bootstrap", False)
        if not isinstance(bootstrap, bool):
            raise ValueError("bootstrap must be a boolean")
        n_jobs = parameters.get("n_jobs", -1)
        if isinstance(n_jobs, bool) or not isinstance(n_jobs, int) or n_jobs < -1:
            raise ValueError("n_jobs must be an integer greater than or equal to -1")
        return {
            "n_estimators": n_estimators,
            "contamination": contamination,
            "random_state": random_state,
            "max_samples": max_samples,
            "max_features": float(max_features),
            "bootstrap": bootstrap,
            "n_jobs": n_jobs,
        }

    @classmethod
    def minimum_history(cls, parameters: dict[str, Any]) -> int:
        return 20

    def fit(
        self,
        values: FloatMatrix,
        *,
        parameters: dict[str, Any],
        missing_values_strategy: str,
        scaling: str,
    ) -> Pipeline:
        config = self.validate_config(parameters)
        steps = self._preprocessing_steps(missing_values_strategy, scaling)
        steps.append(
            (
                "detector",
                IsolationForest(
                    n_estimators=config["n_estimators"],
                    contamination=config["contamination"],
                    max_samples=config["max_samples"],
                    max_features=config["max_features"],
                    bootstrap=config["bootstrap"],
                    random_state=config["random_state"],
                    n_jobs=config["n_jobs"],
                ),
            )
        )
        return Pipeline(steps).fit(values)

    def score(self, model: Pipeline, values: FloatMatrix) -> ScoreVector:
        estimator: IsolationForest = model.named_steps["detector"]
        return -estimator.score_samples(model[:-1].transform(values))

    def predict(self, model: Pipeline, values: FloatMatrix) -> PredictionVector:
        estimator: IsolationForest = model.named_steps["detector"]
        return estimator.predict(model[:-1].transform(values))


class ZScoreDetector(Detector):
    algorithm = "z_score"
    display_name = "Z-score"
    parameter_definitions = [
        {"name": "threshold", "type": "number", "default": 3.0, "minimum": 0.001},
        {"name": "random_state", "type": "integer", "default": 42, "minimum": 0, "maximum": 2147483647},
    ]

    @classmethod
    def validate_config(cls, parameters: dict[str, Any]) -> dict[str, Any]:
        threshold = parameters.get("threshold", 3.0)
        random_state = parameters.get("random_state", 42)
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not np.isfinite(threshold) or threshold <= 0:
            raise ValueError("threshold must be a positive finite number")
        if isinstance(random_state, bool) or not isinstance(random_state, int) or random_state < 0:
            raise ValueError("random_state must be a non-negative integer")
        return {
            "threshold": float(threshold),
            "random_state": random_state,
        }

    @classmethod
    def minimum_history(cls, parameters: dict[str, Any]) -> int:
        return 8

    def fit(
        self,
        values: FloatMatrix,
        *,
        parameters: dict[str, Any],
        missing_values_strategy: str,
        scaling: str,
    ) -> dict[str, Any]:
        config = self.validate_config(parameters)
        preprocessing = Pipeline(
            self._preprocessing_steps(missing_values_strategy, scaling)
        )
        matrix = preprocessing.fit_transform(np.asarray(values, dtype=float))
        centers = np.median(matrix, axis=0)
        deviations = np.median(np.abs(matrix - centers), axis=0) * 1.4826
        standard_deviations = np.std(matrix, axis=0)
        scales = np.where(deviations > 0, deviations, standard_deviations)
        scales = np.where(scales > 0, scales, 1.0)
        return {
            "preprocessing": preprocessing,
            "centers": centers,
            "scales": scales,
            "threshold": config["threshold"],
        }

    def score(self, model: dict[str, Any], values: FloatMatrix) -> ScoreVector:
        matrix = model["preprocessing"].transform(np.asarray(values, dtype=float))
        return np.max(
            np.abs((matrix - model["centers"]) / model["scales"]),
            axis=1,
        )

    def predict(self, model: dict[str, Any], values: FloatMatrix) -> PredictionVector:
        scores = self.score(model, values)
        return np.where(scores > model["threshold"], -1, 1)


class LocalOutlierFactorDetector(Detector):
    algorithm = "local_outlier_factor"
    display_name = "Local Outlier Factor"
    uses_walk_forward = True
    parameter_definitions = [
        {"name": "n_neighbors", "type": "integer", "default": 20, "minimum": 2, "maximum": 1000},
        {"name": "refit_every", "type": "integer", "default": 256, "minimum": 1, "maximum": 100000},
        {"name": "training_window", "type": "integer", "default": 5000, "minimum": 20, "maximum": 100000},
        {"name": "random_state", "type": "integer", "default": 42, "minimum": 0, "maximum": 2147483647},
    ]

    @classmethod
    def validate_config(cls, parameters: dict[str, Any]) -> dict[str, Any]:
        n_neighbors = parameters.get("n_neighbors", 20)
        if isinstance(n_neighbors, bool) or not isinstance(n_neighbors, int) or not 2 <= n_neighbors <= 1000:
            raise ValueError("n_neighbors must be an integer between 2 and 1000")
        refit_every = parameters.get("refit_every", 256)
        if isinstance(refit_every, bool) or not isinstance(refit_every, int) or not 1 <= refit_every <= 100000:
            raise ValueError("refit_every must be an integer between 1 and 100000")
        training_window = parameters.get("training_window", 5000)
        minimum_training_window = max(20, n_neighbors + 1)
        if (
            isinstance(training_window, bool)
            or not isinstance(training_window, int)
            or not minimum_training_window <= training_window <= 100000
        ):
            raise ValueError(
                "training_window must be at least n_neighbors + 1 and no greater than 100000"
            )
        random_state = parameters.get("random_state", 42)
        if isinstance(random_state, bool) or not isinstance(random_state, int) or random_state < 0:
            raise ValueError("random_state must be a non-negative integer")
        return {
            "n_neighbors": n_neighbors,
            "refit_every": refit_every,
            "training_window": training_window,
            "random_state": random_state,
        }

    @classmethod
    def minimum_history(cls, parameters: dict[str, Any]) -> int:
        return max(20, cls.validate_config(parameters)["n_neighbors"] + 1)

    def fit(
        self,
        values: FloatMatrix,
        *,
        parameters: dict[str, Any],
        missing_values_strategy: str,
        scaling: str,
    ) -> Pipeline:
        config = self.validate_config(parameters)
        steps = self._preprocessing_steps(missing_values_strategy, scaling)
        steps.append(
            (
                "detector",
                LocalOutlierFactor(
                    n_neighbors=config["n_neighbors"],
                    novelty=True,
                    contamination="auto",
                ),
            )
        )
        return Pipeline(steps).fit(values)

    def score(self, model: Pipeline, values: FloatMatrix) -> ScoreVector:
        estimator: LocalOutlierFactor = model.named_steps["detector"]
        return -estimator.score_samples(model[:-1].transform(values))

    def predict(self, model: Pipeline, values: FloatMatrix) -> PredictionVector:
        estimator: LocalOutlierFactor = model.named_steps["detector"]
        return estimator.predict(model[:-1].transform(values))


class OneClassSVMDetector(Detector):
    algorithm = "one_class_svm"
    display_name = "One-Class SVM"
    uses_walk_forward = True
    parameter_definitions = [
        {"name": "nu", "type": "number", "default": 0.05, "minimum": 0.001, "maximum": 0.5},
        {"name": "gamma", "type": "string_or_number", "default": "scale", "choices": ["scale", "auto"], "minimum": 0.000001},
        {"name": "kernel", "type": "string", "default": "rbf", "choices": ["linear", "poly", "rbf", "sigmoid"]},
        {"name": "refit_every", "type": "integer", "default": 256, "minimum": 1, "maximum": 100000},
        {"name": "training_window", "type": "integer", "default": 5000, "minimum": 20, "maximum": 100000},
        {"name": "random_state", "type": "integer", "default": 42, "minimum": 0, "maximum": 2147483647},
    ]

    @classmethod
    def validate_config(cls, parameters: dict[str, Any]) -> dict[str, Any]:
        nu = parameters.get("nu", 0.05)
        gamma = parameters.get("gamma", "scale")
        kernel = parameters.get("kernel", "rbf")
        random_state = parameters.get("random_state", 42)
        if isinstance(nu, bool) or not isinstance(nu, (int, float)) or not np.isfinite(nu) or not 0 < nu <= 0.5:
            raise ValueError("nu must be a positive finite number no greater than 0.5")
        if isinstance(gamma, bool) or not (
            gamma in {"scale", "auto"}
            if isinstance(gamma, str)
            else isinstance(gamma, (int, float)) and np.isfinite(gamma) and gamma > 0
        ):
            raise ValueError("gamma must be scale, auto, or a positive finite number")
        if not isinstance(kernel, str) or kernel not in {"linear", "poly", "rbf", "sigmoid"}:
            raise ValueError("kernel must be linear, poly, rbf, or sigmoid")
        refit_every = parameters.get("refit_every", 256)
        if isinstance(refit_every, bool) or not isinstance(refit_every, int) or not 1 <= refit_every <= 100000:
            raise ValueError("refit_every must be an integer between 1 and 100000")
        training_window = parameters.get("training_window", 5000)
        if isinstance(training_window, bool) or not isinstance(training_window, int) or not 20 <= training_window <= 100000:
            raise ValueError("training_window must be an integer between 20 and 100000")
        if isinstance(random_state, bool) or not isinstance(random_state, int) or random_state < 0:
            raise ValueError("random_state must be a non-negative integer")
        return {
            "nu": float(nu),
            "gamma": gamma,
            "kernel": kernel,
            "refit_every": refit_every,
            "training_window": training_window,
            "random_state": random_state,
        }

    @classmethod
    def minimum_history(cls, parameters: dict[str, Any]) -> int:
        cls.validate_config(parameters)
        return 20

    def fit(
        self,
        values: FloatMatrix,
        *,
        parameters: dict[str, Any],
        missing_values_strategy: str,
        scaling: str,
    ) -> Pipeline:
        config = self.validate_config(parameters)
        steps = self._preprocessing_steps(missing_values_strategy, scaling)
        steps.append(
            (
                "detector",
                OneClassSVM(
                    nu=config["nu"],
                    gamma=config["gamma"],
                    kernel=config["kernel"],
                ),
            )
        )
        return Pipeline(steps).fit(values)

    def score(self, model: Pipeline, values: FloatMatrix) -> ScoreVector:
        estimator: OneClassSVM = model.named_steps["detector"]
        return -estimator.decision_function(model[:-1].transform(values))

    def predict(self, model: Pipeline, values: FloatMatrix) -> PredictionVector:
        estimator: OneClassSVM = model.named_steps["detector"]
        return estimator.predict(model[:-1].transform(values))


@dataclass(slots=True)
class _AutoencoderModel:
    preprocessing: Pipeline
    estimator: MLPRegressor
    threshold: float
    training_scores: ScoreVector


class AutoencoderDetector(Detector):
    algorithm = "autoencoder"
    display_name = "Autoencoder (MLP)"
    uses_walk_forward = True
    parameter_definitions = [
        {"name": "hidden_layer_sizes", "type": "integer_list", "default": [32, 16], "minimum": 1, "maximum": 512},
        {"name": "max_iter", "type": "integer", "default": 300, "minimum": 50, "maximum": 2000},
        {"name": "alpha", "type": "number", "default": 0.0001, "minimum": 0},
        {"name": "threshold_percentile", "type": "number", "default": 99.0, "minimum": 90, "maximum": 99.99},
        {"name": "refit_every", "type": "integer", "default": 256, "minimum": 1, "maximum": 100000},
        {"name": "training_window", "type": "integer", "default": 5000, "minimum": 30, "maximum": 100000},
        {"name": "random_state", "type": "integer", "default": 42, "minimum": 0, "maximum": 2147483647},
    ]

    @classmethod
    def validate_config(cls, parameters: dict[str, Any]) -> dict[str, Any]:
        hidden = parameters.get("hidden_layer_sizes", [32, 16])
        if (
            not isinstance(hidden, (list, tuple))
            or not hidden
            or any(
                isinstance(size, bool)
                or not isinstance(size, int)
                or not 1 <= size <= 512
                for size in hidden
            )
        ):
            raise ValueError("hidden_layer_sizes must be a non-empty list of integers from 1 to 512")
        max_iter = parameters.get("max_iter", 300)
        if isinstance(max_iter, bool) or not isinstance(max_iter, int) or not 50 <= max_iter <= 2000:
            raise ValueError("max_iter must be an integer between 50 and 2000")
        alpha = parameters.get("alpha", 0.0001)
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not np.isfinite(alpha) or alpha < 0:
            raise ValueError("alpha must be a non-negative finite number")
        percentile = parameters.get("threshold_percentile", 99.0)
        if isinstance(percentile, bool) or not isinstance(percentile, (int, float)) or not np.isfinite(percentile) or not 90 <= percentile <= 99.99:
            raise ValueError("threshold_percentile must be between 90 and 99.99")
        refit_every = parameters.get("refit_every", 256)
        if isinstance(refit_every, bool) or not isinstance(refit_every, int) or not 1 <= refit_every <= 100000:
            raise ValueError("refit_every must be an integer between 1 and 100000")
        training_window = parameters.get("training_window", 5000)
        if isinstance(training_window, bool) or not isinstance(training_window, int) or not 30 <= training_window <= 100000:
            raise ValueError("training_window must be an integer between 30 and 100000")
        random_state = parameters.get("random_state", 42)
        if isinstance(random_state, bool) or not isinstance(random_state, int) or random_state < 0:
            raise ValueError("random_state must be a non-negative integer")
        return {
            "hidden_layer_sizes": list(hidden),
            "max_iter": max_iter,
            "alpha": float(alpha),
            "threshold_percentile": float(percentile),
            "refit_every": refit_every,
            "training_window": training_window,
            "random_state": random_state,
        }

    @classmethod
    def minimum_history(cls, parameters: dict[str, Any]) -> int:
        cls.validate_config(parameters)
        return 30

    def fit(
        self,
        values: FloatMatrix,
        *,
        parameters: dict[str, Any],
        missing_values_strategy: str,
        scaling: str,
    ) -> _AutoencoderModel:
        config = self.validate_config(parameters)
        preprocessing = Pipeline(
            self._preprocessing_steps(missing_values_strategy, scaling)
        )
        normalized = preprocessing.fit_transform(np.asarray(values, dtype=np.float64))
        estimator = MLPRegressor(
            hidden_layer_sizes=tuple(config["hidden_layer_sizes"]),
            max_iter=config["max_iter"],
            alpha=config["alpha"],
            random_state=config["random_state"],
            solver="adam",
        )
        target = normalized.ravel() if normalized.shape[1] == 1 else normalized
        estimator.fit(normalized, target)
        training_scores = np.mean(
            np.square(normalized - estimator.predict(normalized)),
            axis=1,
        )
        threshold = float(
            np.percentile(training_scores, config["threshold_percentile"])
        )
        return _AutoencoderModel(preprocessing, estimator, threshold, training_scores)

    def score(self, model: _AutoencoderModel, values: FloatMatrix) -> ScoreVector:
        normalized = model.preprocessing.transform(np.asarray(values, dtype=np.float64))
        reconstructed = model.estimator.predict(normalized)
        if reconstructed.ndim == 1:
            reconstructed = reconstructed.reshape(-1, 1)
        return np.mean(np.square(normalized - reconstructed), axis=1)

    def predict(
        self, model: _AutoencoderModel, values: FloatMatrix
    ) -> PredictionVector:
        return np.where(self.score(model, values) > model.threshold, -1, 1)

    def decision_threshold(self, model: _AutoencoderModel) -> float:
        return model.threshold

    def history_score_reference(self, model: _AutoencoderModel) -> ScoreVector:
        return model.training_scores


@dataclass(slots=True)
class _ControlChartModel:
    preprocessing: Pipeline
    baseline_mean: float
    baseline_std: float
    observations_seen: int
    steps_since_baseline: int
    state: float
    positive_sum: float
    negative_sum: float
    positive_start: int | None
    negative_start: int | None
    training_scores: ScoreVector
    parameters: dict[str, Any]


class _ControlChartDetector(Detector):
    uses_walk_forward = True
    univariate_only = True

    @classmethod
    def validate_common_config(
        cls, parameters: dict[str, Any]
    ) -> int:
        baseline_window = parameters.get("baseline_window", 30)
        if isinstance(baseline_window, bool) or not isinstance(baseline_window, int) or not 8 <= baseline_window <= 5000:
            raise ValueError("baseline_window must be an integer between 8 and 5000")
        reset_rule = parameters.get("reset_rule", "signal")
        if reset_rule != "signal":
            raise ValueError("reset_rule must be 'signal'")
        return baseline_window

    @classmethod
    def minimum_history(cls, parameters: dict[str, Any]) -> int:
        return cls.validate_common_config(parameters)

    def decision_threshold(self, model: _ControlChartModel) -> float:
        return float(model.parameters["L" if self.algorithm == "ewma" else "h"])

    def feature_deviations(
        self,
        model: _ControlChartModel,
        history: FloatMatrix,
        values: FloatMatrix,
    ) -> FloatMatrix:
        return np.abs(
            (np.asarray(values, dtype=np.float64) - model.baseline_mean)
            / model.baseline_std
        )

    def history_score_reference(self, model: _ControlChartModel) -> ScoreVector:
        return model.training_scores

    def result_details(
        self, model: _ControlChartModel, values: FloatMatrix
    ) -> list[dict[str, Any]]:
        _, _, details = self._evaluate(model, values)
        return details


class EWMADetector(_ControlChartDetector):
    algorithm = "ewma"
    display_name = "EWMA Control Chart"
    parameter_definitions = [
        {"name": "lambda", "type": "number", "default": 0.2, "minimum": 0.01, "maximum": 1},
        {"name": "L", "type": "number", "default": 3.0, "minimum": 1, "maximum": 10},
        {"name": "baseline_window", "type": "integer", "default": 30, "minimum": 8, "maximum": 5000},
        {"name": "reset_rule", "type": "string", "default": "signal", "choices": ["signal"]},
    ]

    @classmethod
    def validate_config(cls, parameters: dict[str, Any]) -> dict[str, Any]:
        baseline_window = cls.validate_common_config(parameters)
        smoothing = parameters.get("lambda", 0.2)
        limit = parameters.get("L", 3.0)
        if isinstance(smoothing, bool) or not isinstance(smoothing, (int, float)) or not np.isfinite(smoothing) or not 0.01 <= smoothing <= 1:
            raise ValueError("lambda must be between 0.01 and 1")
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not np.isfinite(limit) or not 1 <= limit <= 10:
            raise ValueError("L must be between 1 and 10")
        return {
            "lambda": float(smoothing),
            "L": float(limit),
            "baseline_window": baseline_window,
            "reset_rule": "signal",
        }

    def fit(
        self,
        values: FloatMatrix,
        *,
        parameters: dict[str, Any],
        missing_values_strategy: str,
        scaling: str,
    ) -> _ControlChartModel:
        config = self.validate_config(parameters)
        matrix = np.asarray(values, dtype=np.float64)
        if matrix.ndim != 2 or matrix.shape[1] != 1:
            raise ValueError("EWMA is univariate; select exactly one parameter")
        preprocessing = Pipeline(
            self._preprocessing_steps(missing_values_strategy, scaling)
        )
        normalized = preprocessing.fit_transform(matrix).reshape(-1)
        if len(normalized) < config["baseline_window"]:
            raise ValueError("Insufficient history for the EWMA baseline window")
        baseline = normalized[: config["baseline_window"]]
        mean = float(np.mean(baseline))
        std = float(np.std(baseline, ddof=1))
        if not np.isfinite(std) or std <= 0:
            std = max(abs(mean) * 1e-6, 1e-9)
        model = _ControlChartModel(
            preprocessing=preprocessing,
            baseline_mean=mean,
            baseline_std=std,
            observations_seen=config["baseline_window"] - 1,
            steps_since_baseline=0,
            state=mean,
            positive_sum=0.0,
            negative_sum=0.0,
            positive_start=None,
            negative_start=None,
            training_scores=np.empty(0, dtype=np.float64),
            parameters=config,
        )
        training_values = normalized[config["baseline_window"] :]
        training_scores, _, _ = self._evaluate_normalized(model, training_values)
        model.training_scores = training_scores
        return model

    def _evaluate_normalized(
        self, model: _ControlChartModel, values: NDArray[np.float64]
    ) -> tuple[ScoreVector, PredictionVector, list[dict[str, Any]]]:
        scores: list[float] = []
        predictions: list[int] = []
        details: list[dict[str, Any]] = []
        smoothing = model.parameters["lambda"]
        limit = model.parameters["L"]
        for value in values:
            model.observations_seen += 1
            model.steps_since_baseline += 1
            model.state = smoothing * float(value) + (1 - smoothing) * model.state
            variance_factor = (
                smoothing
                / (2 - smoothing)
                * (1 - (1 - smoothing) ** (2 * model.steps_since_baseline))
            )
            ewma_std = model.baseline_std * np.sqrt(max(variance_factor, 1e-15))
            signed_score = (model.state - model.baseline_mean) / ewma_std
            score = abs(float(signed_score))
            predicted = -1 if score >= limit else 1
            direction = "upward" if signed_score > 0 else "downward"
            if model.state > model.baseline_mean and model.positive_start is None:
                model.positive_start = model.observations_seen
                model.negative_start = None
            elif model.state < model.baseline_mean and model.negative_start is None:
                model.negative_start = model.observations_seen
                model.positive_start = None
            elif np.isclose(model.state, model.baseline_mean):
                model.positive_start = None
                model.negative_start = None
            if predicted == -1:
                details.append({
                    "shift_direction": direction,
                    "shift_started_position": (
                        model.positive_start
                        if direction == "upward"
                        else model.negative_start
                    ),
                    "baseline_mean": model.baseline_mean,
                    "baseline_standard_deviation": model.baseline_std,
                    "lambda": smoothing,
                    "control_limit_width": limit,
                    "reset_rule": "signal",
                })
            else:
                details.append({
                    "shift_direction": None,
                    "shift_started_position": None,
                    "baseline_mean": model.baseline_mean,
                    "baseline_standard_deviation": model.baseline_std,
                    "lambda": smoothing,
                    "control_limit_width": limit,
                    "reset_rule": "signal",
                })
                model.state = model.baseline_mean
                model.steps_since_baseline = 0
                model.positive_start = None
                model.negative_start = None
            scores.append(score)
            predictions.append(predicted)
        return (
            np.asarray(scores, dtype=np.float64),
            np.asarray(predictions, dtype=np.int64),
            details,
        )

    def _evaluate(
        self, model: _ControlChartModel, values: FloatMatrix
    ) -> tuple[ScoreVector, PredictionVector, list[dict[str, Any]]]:
        normalized = model.preprocessing.transform(
            np.asarray(values, dtype=np.float64)
        ).reshape(-1)
        replay = _copy_chart_model(model)
        return self._evaluate_normalized(replay, normalized)

    def score(self, model: _ControlChartModel, values: FloatMatrix) -> ScoreVector:
        return self._evaluate(model, values)[0]

    def predict(
        self, model: _ControlChartModel, values: FloatMatrix
    ) -> PredictionVector:
        return self._evaluate(model, values)[1]


class CUSUMDetector(_ControlChartDetector):
    algorithm = "cusum"
    display_name = "CUSUM Control Chart"
    parameter_definitions = [
        {"name": "k", "type": "number", "default": 0.5, "minimum": 0, "maximum": 5},
        {"name": "h", "type": "number", "default": 5.0, "minimum": 1, "maximum": 20},
        {"name": "baseline_window", "type": "integer", "default": 30, "minimum": 8, "maximum": 5000},
        {"name": "reset_rule", "type": "string", "default": "signal", "choices": ["signal"]},
    ]

    @classmethod
    def validate_config(cls, parameters: dict[str, Any]) -> dict[str, Any]:
        baseline_window = cls.validate_common_config(parameters)
        slack = parameters.get("k", 0.5)
        threshold = parameters.get("h", 5.0)
        if isinstance(slack, bool) or not isinstance(slack, (int, float)) or not np.isfinite(slack) or not 0 <= slack <= 5:
            raise ValueError("k must be between 0 and 5")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not np.isfinite(threshold) or not 1 <= threshold <= 20:
            raise ValueError("h must be between 1 and 20")
        return {
            "k": float(slack),
            "h": float(threshold),
            "baseline_window": baseline_window,
            "reset_rule": "signal",
        }

    def fit(
        self,
        values: FloatMatrix,
        *,
        parameters: dict[str, Any],
        missing_values_strategy: str,
        scaling: str,
    ) -> _ControlChartModel:
        config = self.validate_config(parameters)
        matrix = np.asarray(values, dtype=np.float64)
        if matrix.ndim != 2 or matrix.shape[1] != 1:
            raise ValueError("CUSUM is univariate; select exactly one parameter")
        preprocessing = Pipeline(
            self._preprocessing_steps(missing_values_strategy, scaling)
        )
        normalized = preprocessing.fit_transform(matrix).reshape(-1)
        if len(normalized) < config["baseline_window"]:
            raise ValueError("Insufficient history for the CUSUM baseline window")
        baseline = normalized[: config["baseline_window"]]
        mean = float(np.mean(baseline))
        std = float(np.std(baseline, ddof=1))
        if not np.isfinite(std) or std <= 0:
            std = max(abs(mean) * 1e-6, 1e-9)
        model = _ControlChartModel(
            preprocessing=preprocessing,
            baseline_mean=mean,
            baseline_std=std,
            observations_seen=config["baseline_window"] - 1,
            steps_since_baseline=0,
            state=0.0,
            positive_sum=0.0,
            negative_sum=0.0,
            positive_start=None,
            negative_start=None,
            training_scores=np.empty(0, dtype=np.float64),
            parameters=config,
        )
        training_values = normalized[config["baseline_window"] :]
        training_scores, _, _ = self._evaluate_normalized(model, training_values)
        model.training_scores = training_scores
        return model

    def _evaluate_normalized(
        self, model: _ControlChartModel, values: NDArray[np.float64]
    ) -> tuple[ScoreVector, PredictionVector, list[dict[str, Any]]]:
        scores: list[float] = []
        predictions: list[int] = []
        details: list[dict[str, Any]] = []
        slack = model.parameters["k"]
        threshold = model.parameters["h"]
        for value in values:
            model.observations_seen += 1
            standardized = (
                float(value) - model.baseline_mean
            ) / model.baseline_std
            if model.positive_sum == 0 and standardized > slack:
                model.positive_start = model.observations_seen
            if model.negative_sum == 0 and standardized < -slack:
                model.negative_start = model.observations_seen
            model.positive_sum = max(
                0.0, model.positive_sum + standardized - slack
            )
            model.negative_sum = min(
                0.0, model.negative_sum + standardized + slack
            )
            upward = model.positive_sum >= threshold
            downward = -model.negative_sum >= threshold
            predicted = -1 if upward or downward else 1
            direction = "upward" if upward else "downward" if downward else None
            start = (
                model.positive_start if upward else model.negative_start if downward else None
            )
            scores.append(max(model.positive_sum, -model.negative_sum))
            predictions.append(predicted)
            details.append({
                "shift_direction": direction,
                "shift_started_position": start,
                "baseline_mean": model.baseline_mean,
                "baseline_standard_deviation": model.baseline_std,
                "k": slack,
                "h": threshold,
                "reset_rule": "signal",
            })
            if predicted == -1:
                model.positive_sum = 0.0
                model.negative_sum = 0.0
                model.positive_start = None
                model.negative_start = None
        return (
            np.asarray(scores, dtype=np.float64),
            np.asarray(predictions, dtype=np.int64),
            details,
        )

    def _evaluate(
        self, model: _ControlChartModel, values: FloatMatrix
    ) -> tuple[ScoreVector, PredictionVector, list[dict[str, Any]]]:
        normalized = model.preprocessing.transform(
            np.asarray(values, dtype=np.float64)
        ).reshape(-1)
        replay = _copy_chart_model(model)
        return self._evaluate_normalized(replay, normalized)

    def score(self, model: _ControlChartModel, values: FloatMatrix) -> ScoreVector:
        return self._evaluate(model, values)[0]

    def predict(
        self, model: _ControlChartModel, values: FloatMatrix
    ) -> PredictionVector:
        return self._evaluate(model, values)[1]


def _copy_chart_model(model: _ControlChartModel) -> _ControlChartModel:
    return _ControlChartModel(
        preprocessing=model.preprocessing,
        baseline_mean=model.baseline_mean,
        baseline_std=model.baseline_std,
        observations_seen=model.observations_seen,
        steps_since_baseline=model.steps_since_baseline,
        state=model.state,
        positive_sum=model.positive_sum,
        negative_sum=model.negative_sum,
        positive_start=model.positive_start,
        negative_start=model.negative_start,
        training_scores=model.training_scores,
        parameters=model.parameters,
    )


def combine_ensemble_votes(
    votes: list[dict[str, Any]], configuration: dict[str, Any]
) -> tuple[bool | None, float, str, bool]:
    eligible_count = len(votes)
    if eligible_count < configuration["quorum"]:
        return None, 0.0, "insufficient_history", False
    anomalous_count = sum(bool(vote["vote"]) for vote in votes)
    total_weight = sum(float(vote["weight"]) for vote in votes)
    anomaly_weight = sum(
        float(vote["weight"]) for vote in votes if bool(vote["vote"])
    )
    mode = configuration["voting_mode"]
    if mode == "quorum":
        combined_vote = anomalous_count >= configuration["quorum"]
    elif mode == "weighted":
        combined_vote = anomaly_weight > total_weight / 2
    else:
        combined_vote = anomalous_count > eligible_count / 2
    agreeing_weight = (
        anomaly_weight if combined_vote else total_weight - anomaly_weight
    )
    confidence = agreeing_weight / total_weight if total_weight else 0.0
    high_confidence = bool(
        combined_vote
        and anomalous_count >= configuration["quorum"]
        and confidence >= configuration["high_confidence_threshold"]
    )
    return combined_vote, float(confidence), "ready", high_confidence


class EnsembleDetector(Detector):
    algorithm = "ensemble"
    display_name = "Detector Ensemble"
    uses_walk_forward = True
    parameter_definitions = [
        {"name": "members", "type": "json", "default": [
            {"algorithm": "isolation_forest", "parameters": {}, "features": [], "weight": 1.0},
            {"algorithm": "z_score", "parameters": {}, "features": [], "weight": 1.0},
        ]},
        {"name": "voting_mode", "type": "string", "default": "majority", "choices": ["majority", "weighted", "quorum"]},
        {"name": "quorum", "type": "integer", "default": 2, "minimum": 2},
        {"name": "high_confidence_threshold", "type": "number", "default": 0.75, "minimum": 0.5, "maximum": 1},
    ]

    @classmethod
    def validate_config(cls, parameters: dict[str, Any]) -> dict[str, Any]:
        default_members = [
            {"algorithm": "isolation_forest", "parameters": {}, "features": [], "weight": 1.0},
            {"algorithm": "z_score", "parameters": {}, "features": [], "weight": 1.0},
        ]
        raw_members = parameters.get("members", default_members)
        if not isinstance(raw_members, list) or len(raw_members) < 2:
            raise ValueError("An ensemble requires at least two detector members")
        members: list[dict[str, Any]] = []
        seen_algorithms: set[str] = set()
        for item in raw_members:
            if not isinstance(item, dict):
                raise ValueError("Each ensemble member must be an object")
            raw_algorithm = item.get("algorithm", "")
            if not isinstance(raw_algorithm, str):
                raise ValueError("Each ensemble member algorithm must be a string")
            algorithm = normalize_algorithm_name(raw_algorithm)
            detector_class = DETECTOR_REGISTRY.get(algorithm)
            if detector_class is None or algorithm == cls.algorithm:
                raise ValueError(f"Unsupported ensemble member: {algorithm}")
            if algorithm in seen_algorithms:
                raise ValueError("An ensemble cannot contain the same detector more than once")
            seen_algorithms.add(algorithm)
            detector_parameters = item.get("parameters", {})
            if not isinstance(detector_parameters, dict):
                raise ValueError(f"Parameters for {algorithm} must be an object")
            validated_parameters = detector_class.validate_config(detector_parameters)
            selected_features = item.get("features", [])
            if (
                not isinstance(selected_features, list)
                or any(
                    isinstance(feature, bool)
                    or not isinstance(feature, (str, int))
                    or (isinstance(feature, int) and feature < 0)
                    for feature in selected_features
                )
                or len(set(selected_features)) != len(selected_features)
            ):
                raise ValueError(f"Features for {algorithm} must be unique names or indexes")
            if detector_class in {EWMADetector, CUSUMDetector} and len(selected_features) > 1:
                raise ValueError(f"{algorithm} requires exactly one selected feature")
            feature_indices = item.get("feature_indices")
            if feature_indices is not None and (
                not isinstance(feature_indices, list)
                or any(
                    isinstance(index, bool) or not isinstance(index, int) or index < 0
                    for index in feature_indices
                )
                or len(set(feature_indices)) != len(feature_indices)
            ):
                raise ValueError(f"Feature indexes for {algorithm} must be unique non-negative integers")
            weight = item.get("weight", 1.0)
            if (
                isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or not np.isfinite(weight)
                or weight <= 0
            ):
                raise ValueError(f"Weight for {algorithm} must be a positive finite number")
            normalized_member = {
                "algorithm": algorithm,
                "parameters": validated_parameters,
                "features": selected_features,
                "weight": float(weight),
            }
            if feature_indices is not None:
                normalized_member["feature_indices"] = feature_indices
            members.append(normalized_member)
        voting_mode = parameters.get("voting_mode", "majority")
        if not isinstance(voting_mode, str) or voting_mode not in {
            "majority", "weighted", "quorum"
        }:
            raise ValueError("voting_mode must be majority, weighted, or quorum")
        quorum = parameters.get("quorum", 2)
        if isinstance(quorum, bool) or not isinstance(quorum, int) or not 2 <= quorum <= len(members):
            raise ValueError("quorum must be an integer between 2 and the member count")
        threshold = parameters.get("high_confidence_threshold", 0.75)
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not np.isfinite(threshold)
            or not 0.5 <= threshold <= 1
        ):
            raise ValueError("high_confidence_threshold must be between 0.5 and 1")
        return {
            "members": members,
            "voting_mode": voting_mode,
            "quorum": quorum,
            "high_confidence_threshold": float(threshold),
            "confidence_rule": "weighted_share_agreeing",
        }

    @classmethod
    def minimum_history(cls, parameters: dict[str, Any]) -> int:
        config = cls.validate_config(parameters)
        requirements = [
            DETECTOR_REGISTRY[member["algorithm"]].minimum_history(member["parameters"])
            for member in config["members"]
        ]
        return min(requirements)

    def fit(
        self,
        values: FloatMatrix,
        *,
        parameters: dict[str, Any],
        missing_values_strategy: str,
        scaling: str,
    ) -> dict[str, Any]:
        config = self.validate_config(parameters)
        matrix = np.asarray(values, dtype=np.float64)
        fitted_members: list[dict[str, Any]] = []
        member_states: list[dict[str, Any]] = []
        for member in config["members"]:
            indices = member.get("feature_indices", member["features"])
            if not indices:
                indices = list(range(matrix.shape[1]))
            if any(not isinstance(index, int) or index < 0 or index >= matrix.shape[1] for index in indices):
                raise ValueError(f"Feature indexes for {member['algorithm']} are outside the matrix")
            detector_class = DETECTOR_REGISTRY[member["algorithm"]]
            detector = detector_class()
            required = detector.minimum_history(member["parameters"])
            member_values = matrix[:, indices]
            eligible = (
                np.isfinite(member_values).all(axis=1)
                if missing_values_strategy == "drop"
                else np.isfinite(member_values).any(axis=1)
            )
            member_history = member_values[eligible]
            member_state = {
                **member,
                "feature_indices": indices,
                "history_size": len(member_history),
                "history_required": required,
                "execution_mode": (
                    "walk_forward"
                    if detector_class.uses_walk_forward
                    else "batch"
                ),
            }
            member_states.append(member_state)
            if len(member_history) < required:
                continue
            model = detector.fit(
                member_history,
                parameters=member["parameters"],
                missing_values_strategy=missing_values_strategy,
                scaling=scaling,
            )
            fitted_members.append({
                **member_state,
                "detector_instance": detector,
                "model": model,
            })
        return {
            "configuration": config,
            "members": fitted_members,
            "member_states": member_states,
            "matrix_width": matrix.shape[1],
            "missing_values_strategy": missing_values_strategy,
        }

    def _evaluate(
        self, model: dict[str, Any], values: FloatMatrix
    ) -> tuple[ScoreVector, PredictionVector, list[dict[str, Any]]]:
        matrix = np.asarray(values, dtype=np.float64)
        config = model["configuration"]
        votes_by_row: list[dict[str, dict[str, Any]]] = [
            {} for _ in range(len(matrix))
        ]
        unavailable_by_row: list[dict[str, str]] = [
            {} for _ in range(len(matrix))
        ]
        for member in model["member_states"]:
            member_matrix = matrix[:, member["feature_indices"]]
            row_validity = (
                np.isfinite(member_matrix).all(axis=1)
                if model["missing_values_strategy"] == "drop"
                else np.isfinite(member_matrix).any(axis=1)
            )
            for position in np.flatnonzero(~row_validity):
                unavailable_by_row[int(position)][member["algorithm"]] = (
                    "excluded_missing_values"
                )
        for member in model["members"]:
            member_matrix = matrix[:, member["feature_indices"]]
            row_validity = (
                np.isfinite(member_matrix).all(axis=1)
                if model["missing_values_strategy"] == "drop"
                else np.isfinite(member_matrix).any(axis=1)
            )
            valid_rows = np.flatnonzero(row_validity)
            if not len(valid_rows):
                continue
            detector = member["detector_instance"]
            component_model = member["model"]
            member_values = member_matrix[valid_rows]
            member_scores = detector.score(component_model, member_values)
            member_predictions = detector.predict(component_model, member_values)
            for position, member_score, prediction in zip(
                valid_rows, member_scores, member_predictions, strict=True
            ):
                votes_by_row[int(position)][member["algorithm"]] = {
                    "algorithm": member["algorithm"],
                    "score": float(member_score),
                    "vote": bool(prediction == -1),
                    "history_state": "ready",
                    "history_size": member["history_size"],
                    "history_required": member["history_required"],
                    "execution_mode": (
                        "walk_forward"
                        if DETECTOR_REGISTRY[member["algorithm"]].uses_walk_forward
                        else "batch"
                    ),
                    "weight": member["weight"],
                }

        row_details: list[dict[str, Any]] = []
        scores: list[float] = []
        predictions: list[int] = []
        for row_votes in votes_by_row:
            votes = list(row_votes.values())
            eligible_count = len(votes)
            history_eligible_count = sum(
                member["history_size"] >= member["history_required"]
                for member in model["member_states"]
            )
            anomalous_count = sum(item["vote"] for item in votes)
            total_weight = sum(item["weight"] for item in votes)
            anomaly_weight = sum(item["weight"] for item in votes if item["vote"])
            combined_vote, confidence, state, high_confidence = (
                combine_ensemble_votes(votes, config)
            )
            prediction = -1 if combined_vote else 1
            score = anomaly_weight / total_weight if total_weight else 0.0
            details_by_algorithm: dict[str, dict[str, Any]] = {}
            for member in model["member_states"]:
                is_fitted = member["algorithm"] in row_votes
                details_by_algorithm[member["algorithm"]] = row_votes.get(
                    member["algorithm"],
                    {
                        "algorithm": member["algorithm"],
                        "score": None,
                        "vote": None,
                        "history_state": (
                            unavailable_by_row[len(row_details)].get(
                                member["algorithm"],
                                "insufficient_history" if not is_fitted else "excluded_missing_values",
                            )
                            if not is_fitted
                            else "excluded_missing_values"
                        ),
                        "history_size": member["history_size"],
                        "history_required": member["history_required"],
                        "execution_mode": member["execution_mode"],
                        "weight": member["weight"],
                    },
                )
            row_details.append({
                "ensemble_state": state,
                "voting_mode": config["voting_mode"],
                "fired_rule": (
                    "insufficient eligible detector history"
                    if combined_vote is None
                    else f"{config['voting_mode']} vote"
                ),
                "voted_count": eligible_count,
                "eligible_count": history_eligible_count,
                "configured_count": len(config["members"]),
                "agreement_count": max(anomalous_count, eligible_count - anomalous_count),
                "vote": combined_vote,
                "confidence": float(confidence),
                "high_confidence": high_confidence,
                "confidence_rule": config["confidence_rule"],
                "configuration": config,
                "detectors": list(details_by_algorithm.values()),
            })
            scores.append(score)
            predictions.append(prediction)
        return (
            np.asarray(scores, dtype=np.float64),
            np.asarray(predictions, dtype=np.int64),
            row_details,
        )

    def score(self, model: dict[str, Any], values: FloatMatrix) -> ScoreVector:
        return self._evaluate(model, values)[0]

    def predict(self, model: dict[str, Any], values: FloatMatrix) -> PredictionVector:
        return self._evaluate(model, values)[1]

    def result_details(
        self, model: dict[str, Any], values: FloatMatrix
    ) -> list[dict[str, Any]]:
        return self._evaluate(model, values)[2]

    def history_score_reference(self, model: dict[str, Any]) -> ScoreVector:
        return np.asarray([0.0], dtype=np.float64)


DETECTOR_REGISTRY: dict[str, type[Detector]] = {
    detector.algorithm: detector
    for detector in (
        IsolationForestDetector,
        ZScoreDetector,
        LocalOutlierFactorDetector,
        OneClassSVMDetector,
        AutoencoderDetector,
        EWMADetector,
        CUSUMDetector,
        EnsembleDetector,
    )
}


def normalize_algorithm_name(algorithm: str) -> str:
    normalized = algorithm.casefold().replace("-", "_").replace(" ", "_")
    aliases = {"zscore": "z_score", "lof": "local_outlier_factor"}
    return aliases.get(normalized, normalized)


def get_detector(algorithm: str) -> Detector:
    normalized = normalize_algorithm_name(algorithm)
    try:
        return DETECTOR_REGISTRY[normalized]()
    except KeyError as error:
        raise ValueError(f"Unsupported analysis algorithm: {algorithm}") from error


def detector_catalog() -> list[dict[str, Any]]:
    return [detector.catalog_entry() for detector in DETECTOR_REGISTRY.values()]


def resolve_ensemble_features(
    parameters: dict[str, Any], feature_names: list[str]
) -> dict[str, Any]:
    config = EnsembleDetector.validate_config(parameters)
    resolved_members: list[dict[str, Any]] = []
    for member in config["members"]:
        selected = member["features"] or feature_names
        indices: list[int] = []
        for feature in selected:
            if isinstance(feature, int) and not isinstance(feature, bool):
                index = feature
                if index >= len(feature_names):
                    raise ValueError(f"Feature index {index} is outside the selected feature list")
            elif isinstance(feature, str) and feature in feature_names:
                index = feature_names.index(feature)
            else:
                raise ValueError(
                    f"Feature {feature!r} for {member['algorithm']} is not selected for this analysis"
                )
            indices.append(index)
        if not indices:
            raise ValueError(f"At least one feature is required for {member['algorithm']}")
        if (
            DETECTOR_REGISTRY[member["algorithm"]] in {EWMADetector, CUSUMDetector}
            and len(indices) != 1
        ):
            raise ValueError(f"{member['algorithm']} requires exactly one selected feature")
        detector_class = DETECTOR_REGISTRY[member["algorithm"]]
        resolved_members.append({
            **member,
            "feature_indices": indices,
            "execution_mode": "ensemble_walk_forward_member_batch",
            "training_history": (
                f"growing history up to the ensemble score batch; "
                f"nested {member['algorithm']} minimum_history="
                f"{detector_class.minimum_history(member['parameters'])}"
            ),
            "vote_rule": (
                "isolation_forest.predict() == -1"
                if member["algorithm"] == "isolation_forest"
                else "z_score.score() > threshold"
            ),
        })
    return {**config, "members": resolved_members}


def walk_forward_predict(
    detector: Detector,
    values: FloatMatrix,
    *,
    parameters: dict[str, Any],
    missing_values_strategy: str,
    scaling: str,
    batch_size: int = 256,
) -> WalkForwardResult:
    if not detector.uses_walk_forward:
        raise ValueError(f"{detector.algorithm} does not use walk-forward prediction")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    matrix = np.asarray(values, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] == 0:
        raise ValueError("Detector input must be a non-empty feature matrix")
    if missing_values_strategy == "drop":
        positions = np.flatnonzero(np.isfinite(matrix).all(axis=1))
    else:
        positions = np.flatnonzero(np.isfinite(matrix).any(axis=1))
    minimum_history = detector.minimum_history(parameters)
    training_window = parameters.get("training_window")
    if (
        isinstance(training_window, int)
        and not isinstance(training_window, bool)
        and training_window < minimum_history
    ):
        raise ValueError(
            "training_window must be at least the detector's minimum_history"
        )
    ensemble_history_ceiling = (
        max(
            DETECTOR_REGISTRY[member["algorithm"]].minimum_history(
                member["parameters"]
            )
            for member in parameters["members"]
        )
        if isinstance(detector, EnsembleDetector)
        else minimum_history
    )
    if getattr(detector, "univariate_only", False) and matrix.shape[1] != 1:
        raise ValueError(f"{detector.algorithm} requires exactly one selected feature")
    scored_positions: list[int] = []
    predictions: list[int] = []
    scores: list[float] = []
    thresholds: list[float] = []
    severity_thresholds: list[tuple[float, float]] = []
    history_sizes: list[int] = []
    feature_deviations: list[FloatMatrix] = []
    result_details: list[dict[str, Any]] = []
    final_model: Any | None = None

    configured_refit_every = parameters.get("refit_every")
    refit_every = configured_refit_every or batch_size
    if isinstance(refit_every, bool) or not isinstance(refit_every, int) or refit_every < 1:
        raise ValueError("refit_every must be a positive integer")
    remaining_readings = len(positions) - minimum_history
    if (
        configured_refit_every is not None
        and not isinstance(detector, EnsembleDetector)
        and remaining_readings > 1
        and refit_every >= remaining_readings
    ):
        raise ValueError(
            "refit_every must be smaller than the number of readings after "
            "minimum history; "
            "that would score the dataset from minimum history only"
        )
    effective_batch_size = min(refit_every, max(1, remaining_readings))
    batch_start = minimum_history
    chart_model: _ControlChartModel | None = None
    chart_history_scores = np.empty(0, dtype=np.float64)
    control_chart_baseline_window = (
        detector.validate_config(parameters)["baseline_window"]
        if isinstance(detector, _ControlChartDetector)
        else None
    )
    while batch_start < len(positions):
        current_batch_size = (
            1
            if isinstance(detector, EnsembleDetector)
            and batch_start < ensemble_history_ceiling
            else effective_batch_size
        )
        batch_end = min(batch_start + current_batch_size, len(positions))
        history_begin = (
            max(0, batch_start - training_window)
            if isinstance(training_window, int)
            else 0
        )
        history_positions = positions[history_begin:batch_start]
        batch_positions = positions[batch_start:batch_end]
        batch_values = matrix[batch_positions]
        is_control_chart = isinstance(detector, _ControlChartDetector)
        if is_control_chart:
            if chart_model is None:
                chart_model = detector.fit(
                    matrix[history_positions],
                    parameters=parameters,
                    missing_values_strategy=missing_values_strategy,
                    scaling=scaling,
                )
                final_model = chart_model
                chart_history_scores = detector.score(
                    chart_model, matrix[history_positions]
                )
            normalized_batch = chart_model.preprocessing.transform(
                np.asarray(batch_values, dtype=np.float64)
            ).reshape(-1)
            batch_scores, batch_predictions, batch_details = (
                detector._evaluate_normalized(chart_model, normalized_batch)
            )
            prefix = matrix[positions[:batch_start], 0]
            baseline_values = matrix[
                positions[:control_chart_baseline_window], 0
            ]
            if np.isfinite(prefix).all() and np.isfinite(baseline_values).all():
                if scaling == "standard":
                    prefix_mean = float(np.mean(prefix))
                    prefix_scale = float(np.std(prefix))
                    if not np.isfinite(prefix_scale) or prefix_scale == 0:
                        prefix_scale = 1.0
                    baseline_values = (baseline_values - prefix_mean) / prefix_scale
                report_baseline_mean = float(np.mean(baseline_values))
                report_baseline_std = float(np.std(baseline_values, ddof=1))
                if (
                    not np.isfinite(report_baseline_std)
                    or report_baseline_std <= 0
                ):
                    report_baseline_std = max(
                        abs(report_baseline_mean) * 1e-6,
                        1e-9,
                    )
                for detail in batch_details:
                    detail["baseline_mean"] = report_baseline_mean
                    detail["baseline_standard_deviation"] = report_baseline_std
            history_scores = (
                chart_history_scores
                if batch_start == minimum_history
                else chart_model.training_scores
            )
        else:
            final_model = detector.fit(
                matrix[history_positions],
                parameters=parameters,
                missing_values_strategy=missing_values_strategy,
                scaling=scaling,
            )
            batch_predictions = detector.predict(final_model, batch_values)
            batch_scores = detector.score(final_model, batch_values)
            history_scores = detector.history_score_reference(final_model)
            if history_scores is None:
                history_scores = detector.score(final_model, matrix[history_positions])
            batch_details = detector.result_details(final_model, batch_values)
        if not len(history_scores):
            history_scores = np.asarray([0.0], dtype=np.float64)
        threshold = detector.decision_threshold(final_model)
        batch_feature_deviations = detector.feature_deviations(
            final_model, matrix[history_positions], batch_values
        )
        scored_positions.extend(int(position) for position in batch_positions)
        predictions.extend(int(value) for value in batch_predictions)
        scores.extend(float(value) for value in batch_scores)
        thresholds.extend([threshold] * len(batch_positions))
        severity_threshold = (
            float(np.quantile(history_scores, 0.95)),
            float(np.quantile(history_scores, 0.995)),
        )
        severity_thresholds.extend([severity_threshold] * len(batch_positions))
        history_sizes.extend([len(history_positions)] * len(batch_positions))
        feature_deviations.extend(batch_feature_deviations)
        for detail in batch_details:
            shifted_detail = dict(detail)
            compact_start = shifted_detail.get("shift_started_position")
            if isinstance(compact_start, int) and 0 <= compact_start < len(positions):
                shifted_detail["shift_started_position"] = int(positions[compact_start])
            else:
                shifted_detail["shift_started_position"] = None
            result_details.append(shifted_detail)
        if is_control_chart:
            chart_model.training_scores = np.concatenate(
                (chart_model.training_scores, batch_scores)
            )
        batch_start = batch_end

    return WalkForwardResult(
        positions=np.asarray(scored_positions, dtype=np.int64),
        predictions=np.asarray(predictions, dtype=np.int64),
        scores=np.asarray(scores, dtype=np.float64),
        score_thresholds=np.asarray(thresholds, dtype=np.float64),
        severity_thresholds=np.asarray(
            severity_thresholds,
            dtype=np.float64,
        ).reshape((-1, 2)),
        history_sizes=np.asarray(history_sizes, dtype=np.int64),
        feature_deviations=(
            np.vstack(feature_deviations)
            if feature_deviations
            else np.empty((0, matrix.shape[1]), dtype=np.float64)
        ),
        result_details=result_details,
        final_model=final_model,
        minimum_history=minimum_history,
    )
