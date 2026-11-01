from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.api.dependencies import get_current_user, get_db
from app.modules.devices.api import router as device_router
from app.modules.devices.api import equipment as equipment_router


DEVICE_ROUTE_CASES = {
    ("GET", "/devices"): (None, {}),
    ("GET", "/devices/choices"): (None, {}),
    ("GET", "/devices/responsible-users"): ([], {}),
    ("POST", "/devices/severity/recompute/dry-run"): ({}, {}),
    ("POST", "/devices/severity/recompute"): ({}, {}),
    ("POST", "/devices/measurement-severity/dry-run"): ({}, {}),
    ("POST", "/devices/measurement-severity/store"): ({}, {}),
    ("POST", "/devices/admin/archive-test-data/dry-run"): ({}, {}),
    ("POST", "/devices/admin/archive-test-data"): ({}, {}),
    ("POST", "/devices"): ({}, {"name": "Bearing 1", "device_code": "BRG-1", "device_type": "bearing", "status": "active"}),
    ("GET", "/devices/{device_id}"): ({}, {}),
    ("PATCH", "/devices/{device_id}/archive"): ({}, {}),
    ("DELETE", "/devices/{device_id}"): (None, {}),
    ("PATCH", "/devices/{device_id}"): ({}, {"name": "Bearing 1", "device_code": "BRG-1", "device_type": "bearing", "status": "active"}),
    ("GET", "/devices/{device_id}/parameters"): ([], {}),
    ("POST", "/devices/{device_id}/parameters"): ({}, {"name": "Temperature", "unit": "C"}),
    ("PATCH", "/devices/{device_id}/parameters/{parameter_id}"): ({}, {"name": "Temperature", "unit": "C"}),
    ("GET", "/devices/{device_id}/measurements"): ({}, {}),
    ("POST", "/devices/{device_id}/measurements"): ({}, {"parameter_id": 1, "value": 1.0}),
}


def _client(user: SimpleNamespace):
    app = FastAPI()
    app.include_router(device_router.router, prefix="/devices")
    app.include_router(equipment_router.router, prefix="/devices")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app)


def test_every_device_route_has_characterisation_coverage():
    app = FastAPI()
    app.include_router(device_router.router, prefix="/devices")
    app.include_router(equipment_router.router, prefix="/devices")
    covered = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/devices")
        for method in operations
    }
    assert set(DEVICE_ROUTE_CASES) <= covered


def test_device_admin_routes_reject_technician_with_403():
    with _client(SimpleNamespace(id=1, role="maintenance_technician", is_active=True)) as client:
        for method, path in (
            ("POST", "/devices/severity/recompute/dry-run"),
            ("POST", "/devices/severity/recompute"),
            ("POST", "/devices/measurement-severity/dry-run"),
            ("POST", "/devices/measurement-severity/store"),
            ("POST", "/devices/admin/archive-test-data/dry-run"),
            ("POST", "/devices/admin/archive-test-data"),
            ("GET", "/devices/responsible-users"),
        ):
            formatted = path.replace("{device_id}", "1").replace("{parameter_id}", "1")
            response = client.request(method, formatted)
            assert response.status_code == 403


def test_device_routes_report_main_validation_error():
    with _client(SimpleNamespace(id=1, role="administrator", is_active=True)) as client:
        cases = (
            ("POST", "/devices", {}),
            ("POST", "/devices/1/parameters", {}),
            ("PATCH", "/devices/1/parameters/1", {}),
            ("POST", "/devices/1/measurements", {}),
        )
        for method, path, body in cases:
            response = client.request(method, path, json=body)
            assert response.status_code == 422
