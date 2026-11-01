import math
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SensorReadingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_identifier: str = Field(
        min_length=1,
        max_length=100,
        description="Exact registered device code associated with the API key.",
    )
    parameter: str = Field(
        min_length=1,
        max_length=100,
        description="Enabled equipment parameter name, matched case-insensitively.",
    )
    value: float = Field(description="Finite measurement in the parameter's unit.")
    unit: str = Field(min_length=1, max_length=30)
    sensor_timestamp: datetime | None = Field(
        default=None,
        description="Optional timezone-aware sensor time; limited to 30 days old and 5 minutes ahead.",
    )
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=60)

    @field_validator("device_identifier", "parameter", "unit")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("This field cannot be blank")
        return normalized

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Sensor reading value must be finite")
        return value

    @field_validator("sensor_timestamp")
    @classmethod
    def require_timezone_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("sensor_timestamp must include a timezone")
        return value


class BatchReadingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=30)
    sensor_timestamp: datetime | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=60)

    @field_validator("parameter", "unit")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("This field cannot be blank")
        return normalized

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Sensor reading value must be finite")
        return value

    @field_validator("sensor_timestamp")
    @classmethod
    def require_timezone_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("sensor_timestamp must include a timezone")
        return value


class SensorReadingBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_identifier: str = Field(min_length=1, max_length=100)
    readings: list[BatchReadingInput] = Field(min_length=1, max_length=100)
