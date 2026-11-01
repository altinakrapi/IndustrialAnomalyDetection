from pathlib import Path
from collections.abc import Callable

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.core.notifications import notify_roles, notify_user
from app.modules.datasets.application.nasa_ims import iter_preprocessed_groups
from app.shared.models import (
    AuditLog,
    Dataset,
    MeasurementRecord,
)


def persist_nasa_ims_archive(
    db: Session,
    archive_path: Path,
    source_filename: str,
    user_id: int,
    window_size: int,
    description: str | None = None,
    on_progress: Callable[[], None] | None = None,
) -> list[Dataset]:
    archive_name = Path(source_filename).stem
    datasets = []
    for group_name, features, measurements in iter_preprocessed_groups(
        str(archive_path), window_size, on_progress
    ):
        name = (
            archive_name
            if group_name == "NASA IMS Bearings"
            else f"{archive_name} - {group_name}"
        )
        dataset = Dataset(
            name=name[:255],
            file_path=str(archive_path),
            file_format="nasa_ims",
            row_count=0,
            invalid_row_count=0,
            column_metadata=[
                {"name": "source_file", "type": "text"},
                {"name": "window_index", "type": "numeric"},
                {"name": "sample_start", "type": "numeric"},
                {"name": "sample_end", "type": "numeric"},
                *[{"name": feature, "type": "numeric"} for feature in features],
            ],
            status="uploaded",
            description=description,
            uploaded_by=user_id,
        )
        db.add(dataset)
        db.flush()
        records = []
        for measurement in measurements:
            measurement["dataset_id"] = dataset.id
            records.append(measurement)
            if len(records) == 1000:
                db.execute(insert(MeasurementRecord), records)
                dataset.row_count += len(records)
                records.clear()
        if records:
            db.execute(insert(MeasurementRecord), records)
            dataset.row_count += len(records)
        if dataset.row_count < 2:
            raise ValueError(
                f"{name} must contain at least two complete vibration windows."
            )
        datasets.append(dataset)

    if not datasets:
        raise ValueError("No NASA IMS test sets were found in the archive.")
    descriptions = "; ".join(
        f"{dataset.name}: {dataset.row_count:,} vibration windows"
        for dataset in datasets
    )
    for dataset in datasets:
        db.add(
            AuditLog(
                user_id=user_id,
                action="upload",
                entity_type="dataset",
                entity_id=dataset.id,
                details={
                    "file_name": source_filename,
                    "row_count": dataset.row_count,
                    "format": "NASA IMS Bearings",
                    "window_size": window_size,
                },
            )
        )
    notify_user(
        db,
        user_id,
        "NASA IMS dataset imported",
        descriptions,
        "dataset_imported",
        "dataset",
        datasets[0].id,
    )
    notify_roles(
        db,
        {"administrator", "data_analyst"},
        "NASA IMS dataset imported",
        descriptions,
        "dataset_imported",
        "dataset",
        datasets[0].id,
        exclude_user_id=user_id,
    )
    return datasets
