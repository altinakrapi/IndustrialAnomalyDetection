import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dependencies import get_current_user, get_db
from app.modules.settings.api.router import SettingsUpdate, settings_payload
from app.modules.settings.api.router import router as settings_router


class StaleAlertSettingsTests(unittest.TestCase):
    def test_stale_interval_multiplier_defaults_to_three(self) -> None:
        db = Mock()
        db.get.return_value = None
        db.scalars.return_value = []

        self.assertEqual(
            settings_payload(db)["stale_data_interval_multiplier"],
            3,
        )

    def test_stale_interval_multiplier_is_configurable_and_bounded(self) -> None:
        self.assertEqual(
            SettingsUpdate(stale_data_interval_multiplier=5)
            .stale_data_interval_multiplier,
            5,
        )
        for invalid_value in (0, 101):
            with self.subTest(value=invalid_value):
                with self.assertRaises(ValidationError):
                    SettingsUpdate(
                        stale_data_interval_multiplier=invalid_value
                    )

    def test_only_administrators_can_update_stale_interval_multiplier(self) -> None:
        app = FastAPI()
        app.include_router(settings_router, prefix="/settings")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=8, role="data_analyst"
        )
        app.dependency_overrides[get_db] = Mock

        with TestClient(app) as client:
            response = client.patch(
                "/settings",
                json={"stale_data_interval_multiplier": 4},
            )

        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
