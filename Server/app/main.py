import asyncio
from contextlib import asynccontextmanager, suppress
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import settings
from app.modules.datasets.application.nasa_ims_initializer import (
    nasa_ims_import_recovery_loop,
)
from app.workers.monitoring_scheduler import monitoring_loop


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler_task = asyncio.create_task(monitoring_loop())
    import_recovery_task = asyncio.create_task(nasa_ims_import_recovery_loop())
    try:
        yield
    finally:
        scheduler_task.cancel()
        import_recovery_task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(scheduler_task, import_recovery_task)

app = FastAPI(
    title="Industrial Anomaly Detection API",
    description="Manage industrial equipment, sensor datasets, anomaly analyses, and maintenance.",
    version="1.0.0",
    lifespan=lifespan,
)

_rate_windows: dict[str, deque[float]] = defaultdict(deque)


@app.middleware("http")
async def rate_limit_sensitive_requests(request: Request, call_next):
    now = time.monotonic()
    path = request.url.path
    limited = (
        (request.method == "POST" and path.endswith("/auth/login"))
        or (request.method in {"POST", "PUT"} and "/readings" in path)
    )
    if limited:
        client = request.client.host if request.client else "unknown"
        key = f"{client}:{path}"
        window = _rate_windows[key]
        while window and now - window[0] > 60:
            window.popleft()
        limit = 10 if "/readings" in path else 5
        if len(window) >= limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests; retry later"},
            )
        window.append(now)
    response = await call_next(request)
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.CORS_ORIGINS.split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}