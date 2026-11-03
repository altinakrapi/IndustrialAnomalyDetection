import asyncio
import inspect

from starlette.requests import Request
from starlette.responses import Response

import app.main as main_module
from app.main import _rate_windows
from app.modules.datasets.api.router import upload_dataset
from app.modules.sensor_ingestion.api import MAX_READINGS_PER_MINUTE
from app.main import verify_sensor_rate_schema


def _request(path: str) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_login_rate_limit_returns_429(monkeypatch):
    _rate_windows.clear()
    monkeypatch.setattr(main_module.time, "monotonic", lambda: 1000.0)

    async def allowed(request):
        return Response(status_code=401)

    async def run():
        middleware = main_module.rate_limit_sensitive_requests
        for _ in range(5):
            response = await middleware(_request("/api/v1/auth/login"), allowed)
            assert response.status_code == 401
        return await middleware(_request("/api/v1/auth/login"), allowed)

    assert asyncio.run(run()).status_code == 429


def test_ingestion_middleware_does_not_apply_device_request_limit(monkeypatch):
    _rate_windows.clear()
    monkeypatch.setattr(main_module.time, "monotonic", lambda: 2000.0)

    async def allowed(request):
        return Response(status_code=201)

    async def run():
        middleware = main_module.rate_limit_sensitive_requests
        for _ in range(20):
            response = await middleware(_request("/api/v1/readings"), allowed)
            assert response.status_code == 201
        return Response(status_code=201)

    assert asyncio.run(run()).status_code == 201


def test_sensor_database_rate_limit_is_bounded():
    assert MAX_READINGS_PER_MINUTE == 120


def test_sensor_rate_schema_reports_missing_migration(monkeypatch):
    class MissingColumnDb:
        def execute(self, _statement):
            return self

        def scalar(self):
            return False

    try:
        verify_sensor_rate_schema(MissingColumnDb())
    except RuntimeError as error:
        assert "run alembic upgrade head" in str(error)
    else:
        raise AssertionError("missing rate_events column did not fail startup")


def test_dataset_upload_function_enforces_configured_limit():
    source = inspect.getsource(upload_dataset)
    assert "max_upload_size_mb" in source
    assert "too_large" in source
