from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_db
from app.main import app


STATIC_ADMIN_ANALYST_ROUTES = (
    "/api/v1/analyses/evaluations/tuning-runs",
    "/api/v1/analyses/evaluations/tuned-configs",
)

STATIC_AUTHENTICATED_ROUTES = (
    "/api/v1/analyses/evaluations",
    "/api/v1/analyses/evaluations/ground-truth",
)


def _client(monkeypatch, role):
    user = SimpleNamespace(id=1, role=role, is_active=True)
    db = MagicMock()
    db.scalars.return_value = []
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: user)
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    return TestClient(app)


@pytest.mark.parametrize("role", ("administrator", "data_analyst"))
@pytest.mark.parametrize("path", STATIC_ADMIN_ANALYST_ROUTES)
def test_tuning_static_routes_are_allowed_for_editors(monkeypatch, role, path):
    with _client(monkeypatch, role) as client:
        response = client.get(path)
    assert response.status_code == 200


@pytest.mark.parametrize("role", ("administrator", "data_analyst", "maintenance_technician"))
@pytest.mark.parametrize("path", STATIC_AUTHENTICATED_ROUTES)
def test_other_static_evaluation_routes_are_not_swallowed(monkeypatch, role, path):
    with _client(monkeypatch, role) as client:
        response = client.get(path)
    assert response.status_code == 200


@pytest.mark.parametrize("path", STATIC_ADMIN_ANALYST_ROUTES)
def test_tuning_static_routes_reject_technician(monkeypatch, path):
    with _client(monkeypatch, "maintenance_technician") as client:
        response = client.get(path)
    assert response.status_code == 403
