from datetime import datetime

from pydantic import BaseModel


class NotificationArchiveInput(BaseModel):
    before: datetime
    notification_type: str = "anomaly_detected"
    related_entity_type: str | None = "anomaly"
    analysis_run_id: int | None = None


class NotificationRestoreInput(BaseModel):
    archived_before: datetime
    notification_type: str = "anomaly_detected"
    related_entity_type: str | None = "anomaly"
    analysis_run_id: int | None = None
