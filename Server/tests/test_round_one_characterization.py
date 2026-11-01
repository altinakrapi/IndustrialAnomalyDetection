import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_db
from app.main import app
from app.modules.maintenance.api.router import router as maintenance_router
from app.modules.reports.api.router import router as reports_router


def test_openapi_matches_committed_snapshot():
    root = Path(__file__).resolve().parents[1]
    snapshot = json.loads((root / "openapi.snapshot.json").read_text(encoding="utf-8"))
    assert json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n" == (
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    )


def test_report_history_characterisation_and_role_restriction():
    api = FastAPI()
    api.include_router(reports_router, prefix="/reports")
    db = Mock()
    db.scalars.return_value = []
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="data_analyst", is_active=True
    )
    with TestClient(api) as client:
        response = client.get("/reports/history")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    api.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=2, role="maintenance_technician", is_active=True
    )
    with TestClient(api) as client:
        response = client.get("/reports/history")
    assert response.status_code == 200


def test_maintenance_list_characterisation_and_forbidden_role():
    api = FastAPI()
    api.include_router(maintenance_router, prefix="/maintenance")
    db = Mock()
    db.scalars.return_value = []
    api.dependency_overrides[get_db] = lambda: db

    api.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="maintenance_technician", is_active=True
    )
    with TestClient(api) as client:
        response = client.get("/maintenance")
    assert response.status_code == 200

    with TestClient(api) as client:
        forbidden = client.post("/maintenance", json={"title": "Pinned", "priority": "medium"})
    assert forbidden.status_code == 403, forbidden.text
    assert isinstance(response.json(), list)

    api.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=2, role="data_analyst", is_active=True
    )
    with TestClient(api) as client:
        response = client.get("/maintenance")
    assert response.status_code == 200
