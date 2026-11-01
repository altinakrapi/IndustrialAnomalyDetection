from pydantic import BaseModel, Field


class MonitoringUpdate(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    simulation_enabled: bool | None = None
    simulation_batch_size: int | None = Field(default=None, ge=2, le=10000)
