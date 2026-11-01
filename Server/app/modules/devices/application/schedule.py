from datetime import datetime, timedelta, timezone

from app.shared.models import EquipmentParameter


def measurement_schedule(
    parameter: EquipmentParameter,
    last_measurement_at: datetime | None,
    now: datetime,
) -> tuple[datetime, str]:
    reference_time = last_measurement_at or parameter.created_at
    if reference_time.tzinfo is None or reference_time.utcoffset() is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    else:
        reference_time = reference_time.astimezone(timezone.utc)
    current_time = (
        now.replace(tzinfo=timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None
        else now.astimezone(timezone.utc)
    )
    due_at = reference_time + timedelta(
        seconds=(
            parameter.expected_interval_seconds
            if parameter.expected_interval_seconds is not None
            else parameter.expected_interval_minutes * 60
        )
    )
    if not parameter.enabled:
        status = "disabled"
    elif due_at <= current_time:
        status = "overdue"
    elif last_measurement_at is None:
        status = "awaiting_first_measurement"
    else:
        status = "scheduled"
    return due_at, status
