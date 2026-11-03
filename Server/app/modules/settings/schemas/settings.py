from pydantic import BaseModel, Field, model_validator


class SettingsUpdate(BaseModel):
    max_upload_size_mb: int | None = Field(default=None, ge=1, le=2048)
    default_n_estimators: int | None = Field(default=None, ge=10, le=1000)
    default_contamination: float | None = Field(default=None, gt=0, le=0.5)
    default_consecutive_anomaly_threshold: int | None = Field(default=None, ge=1, le=100)
    default_persistence_window_size: int | None = Field(default=None, ge=1, le=100)
    default_persistence_anomaly_threshold: int | None = Field(default=None, ge=1, le=100)
    alert_episode_close_normal_count: int | None = Field(default=None, ge=1, le=1000)
    alert_escalation_minutes: int | None = Field(default=None, ge=1, le=525600)
    manual_extreme_z_score_threshold: float | None = Field(default=None, gt=0, le=1000)
    stale_data_interval_multiplier: int | None = Field(default=None, ge=1, le=100)
    notification_device_hourly_limit: int | None = Field(default=None, ge=1, le=10000)
    sensor_ingestion_limit_per_minute: int | None = Field(default=None, ge=1, le=10000)
    sensor_ingestion_global_request_limit_per_minute: int | None = Field(default=None, ge=120, le=100000)

    @model_validator(mode="after")
    def validate_persistence_window(self) -> "SettingsUpdate":
        if (
            self.default_persistence_window_size is not None
            and self.default_persistence_anomaly_threshold is not None
            and self.default_persistence_anomaly_threshold > self.default_persistence_window_size
        ):
            raise ValueError("The persistence anomaly threshold cannot exceed its window size")
        return self
