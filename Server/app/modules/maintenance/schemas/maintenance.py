from datetime import datetime

from pydantic import BaseModel, Field


class MaintenanceOrderInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    device_id: int | None = None
    assigned_to: int | None = None
    priority: str = "medium"
    due_date: datetime | None = None


class MaintenanceUpdate(BaseModel):
    status: str | None = None
    assigned_to: int | None = None
    priority: str | None = None
    due_date: datetime | None = None
    resolution_notes: str | None = None


class ActionInput(BaseModel):
    action_description: str = Field(min_length=1)
    findings: str | None = None
    recommendations: str | None = None
    issue_confirmed: bool | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
