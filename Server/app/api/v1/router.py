from fastapi import APIRouter

from app.modules.analyses.api import router as analyses
from app.modules.anomaly_detection.api import router as anomalies
from app.modules.audit_logs.api import router as audit
from app.modules.dashboard.api import router as dashboard
from app.modules.datasets.api import router as datasets
from app.modules.devices.api import router as devices
from app.modules.devices.api.equipment import router as equipment
from app.modules.maintenance.api import router as maintenance
from app.modules.monitoring.api import router as monitoring
from app.modules.notifications.api import router as notifications
from app.modules.reports.api import router as reports
from app.modules.settings.api import router as settings
from app.modules.sensor_ingestion.api import key_router as sensor_keys
from app.modules.sensor_ingestion.api import router as sensor_readings
from app.modules.users.api import auth, router as users

router = APIRouter()
router.include_router(auth.router, prefix="/auth", tags=["authentication"])
router.include_router(users.router, prefix="/users", tags=["users"])
router.include_router(devices.router, prefix="/devices", tags=["devices"])
router.include_router(equipment, prefix="/devices", tags=["equipment monitoring"])
router.include_router(datasets.router, prefix="/datasets", tags=["datasets"])
router.include_router(analyses.router, prefix="/analyses", tags=["analyses"])
router.include_router(anomalies.router, prefix="/anomalies", tags=["anomalies"])
router.include_router(audit.router, prefix="/audit", tags=["audit"])
router.include_router(
    maintenance.router, prefix="/maintenance", tags=["maintenance"]
)
router.include_router(
    monitoring.router, prefix="/monitoring", tags=["monitoring"]
)
router.include_router(reports.router, prefix="/reports", tags=["reports"])
router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
router.include_router(
    notifications.router, prefix="/notifications", tags=["notifications"]
)
router.include_router(settings.router, prefix="/settings", tags=["settings"])
router.include_router(
    sensor_readings, prefix="/readings", tags=["sensor ingestion"]
)
router.include_router(
    sensor_keys, prefix="/device-api-keys", tags=["device API keys"]
)