import unittest
from types import SimpleNamespace

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_db
from app.modules.analyses.application.equipment_history import (
    score_equipment_history,
)
from app.modules.analyses.api.router import router as analyses_router
from app.modules.analyses.domain.detectors import DETECTOR_REGISTRY


def detector_parameters(algorithm: str) -> dict:
    if algorithm == "isolation_forest":
        return {"n_estimators": 10, "random_state": 3}
    if algorithm == "z_score":
        return {"threshold": 3.0}
    if algorithm == "local_outlier_factor":
        return {
            "n_neighbors": 5,
            "refit_every": 25,
            "training_window": 100,
            "random_state": 3,
        }
    if algorithm == "one_class_svm":
        return {
            "refit_every": 25,
            "training_window": 100,
            "random_state": 3,
        }
    if algorithm == "autoencoder":
        return {
            "hidden_layer_sizes": [4, 2],
            "max_iter": 50,
            "refit_every": 25,
            "training_window": 100,
            "random_state": 3,
        }
    if algorithm == "ewma":
        return {"baseline_window": 30}
    if algorithm == "cusum":
        return {"baseline_window": 30}
    if algorithm == "ensemble":
        return {
            "members": [
                {
                    "algorithm": "isolation_forest",
                    "parameters": {"n_estimators": 10, "random_state": 3},
                    "features": ["Vibration RMS"],
                    "weight": 1.0,
                },
                {
                    "algorithm": "z_score",
                    "parameters": {"threshold": 3.0},
                    "features": ["Vibration RMS"],
                    "weight": 1.0,
                },
            ],
            "voting_mode": "quorum",
            "quorum": 2,
        }
    raise AssertionError(f"Unhandled detector: {algorithm}")


class EquipmentHistoryScoringTests(unittest.TestCase):
    def test_all_registered_detectors_score_chronologically_without_future_data(self) -> None:
        rng = np.random.default_rng(42)
        values = rng.normal(2.4, 0.12, 72)
        values[48:53] += np.linspace(0.1, 0.8, 5)
        values[53:] += 0.8

        for algorithm, detector_class in DETECTOR_REGISTRY.items():
            with self.subTest(algorithm=algorithm):
                result = score_equipment_history(
                    detector_class(),
                    values,
                    parameters=detector_parameters(algorithm),
                    feature_name="Vibration RMS",
                    training_window=100,
                    refit_every=25,
                )
                self.assertEqual(len(result), len(values))
                scored = [item for item in result if item["score"] is not None]
                self.assertTrue(scored)
                self.assertTrue(
                    any(item["history_state"] == "ready" for item in scored)
                )
                for item in scored:
                    self.assertLess(
                        item["details"]["training_end_position"],
                        item["position"],
                    )
                    if algorithm != "ensemble":
                        self.assertEqual(item["history_state"], "ready")

    def test_training_window_must_cover_detector_minimum_history(self) -> None:
        detector = DETECTOR_REGISTRY["ewma"]()
        with self.assertRaisesRegex(ValueError, "training_window"):
            score_equipment_history(
                detector,
                np.ones(40),
                parameters={"baseline_window": 30},
                feature_name="Vibration RMS",
                training_window=29,
                refit_every=5,
            )

    def test_equipment_history_analysis_preserves_analyst_role_boundary(self) -> None:
        app = FastAPI()
        app.include_router(analyses_router, prefix="/analyses")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=9, role="maintenance_technician"
        )
        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        with TestClient(app) as client:
            response = client.post(
                "/analyses/equipment-history",
                json={
                    "device_id": 1,
                    "parameter_id": 1,
                    "algorithm": "ewma",
                    "source_type": "simulated",
                },
            )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
