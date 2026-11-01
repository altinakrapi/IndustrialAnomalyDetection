from collections.abc import Mapping, Sequence
from typing import Any

NASA_IMS_METADATA_COLUMNS = frozenset(
    {"window_index", "sample_start", "sample_end"}
)
NON_MEASUREMENT_COLUMNS = frozenset(
    {"label", "target", "anomaly", "is_anomaly"}
)


def is_analysis_feature(name: str, file_format: str) -> bool:
    normalized = name.strip().lower()
    if normalized in NON_MEASUREMENT_COLUMNS:
        return False
    return not (
        file_format == "nasa_ims"
        and normalized in NASA_IMS_METADATA_COLUMNS
    )


def numeric_analysis_features(
    file_format: str, column_metadata: Sequence[Mapping[str, Any]]
) -> list[str]:
    return [
        name
        for column in column_metadata
        if column.get("type") == "numeric"
        and isinstance((name := column.get("name")), str)
        and is_analysis_feature(name, file_format)
    ]
