import hashlib
import csv
from io import StringIO
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import Float, case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db, require_roles
from app.core.config import settings
from app.core.system_settings import get_system_setting
from app.modules.datasets.application.import_nasa_ims import (
    persist_nasa_ims_archive,
)
from app.modules.datasets.application.nasa_ims_initializer import (
    NASA_IMS_SOURCE_KEY,
    run_nasa_ims_initialization,
)
from app.modules.datasets.application.nasa_ims import (
    archive_entries,
)
from app.modules.datasets.api.nasa_ims_validation import (
    router as nasa_ims_validation_router,
)
from app.shared.models import (
    AnalysisResult,
    AnalysisRun,
    Anomaly,
    AuditLog,
    Dataset,
    DatasetImportJob,
    Device,
    MeasurementRecord,
    ModelConfig,
    User,
)
from app.shared.feature_selection import numeric_analysis_features
from app.workers.analysis_worker import run_analysis

router = APIRouter()
EDITORS = require_roles("administrator", "data_analyst")
router.include_router(
    nasa_ims_validation_router,
    prefix="/nasa-ims/evaluations",
    tags=["NASA IMS validation"],
)
_IMPORT_STALE_AFTER = {
    "queued": timedelta(minutes=5),
    "downloading": timedelta(minutes=15),
    "extracting": timedelta(hours=3),
    "processing": timedelta(hours=3),
}


def dataset_payload(dataset: Dataset) -> dict:
    device = (
        dataset.device
        if dataset.device is not None and dataset.device.is_registered
        else None
    )
    return {
        "id": dataset.id,
        "name": dataset.name,
        "file_name": (
            f"{dataset.name}.zip"
            if dataset.file_format == "nasa_ims"
            else Path(dataset.file_path).name
        ),
        "file_format": dataset.file_format,
        "row_count": dataset.row_count,
        "invalid_row_count": dataset.invalid_row_count,
        "column_metadata": dataset.column_metadata,
        "status": dataset.status,
        "description": dataset.description,
        "device_id": device.id if device else None,
        "device_name": device.name if device else None,
        "uploaded_by": dataset.uploaded_by,
        "uploaded_at": dataset.uploaded_at,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@router.post("", status_code=status.HTTP_201_CREATED)
def upload_dataset(
    device_id: int | None = None,
    file: UploadFile = File(...),
    description: str | None = None,
    window_size: int = 256,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    if device_id is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                "NASA IMS test sets are separate historical experiments and "
                "cannot be assigned to an equipment device."
            ),
        )
    if Path(file.filename or "").suffix.lower() != ".zip":
        raise HTTPException(
            status_code=415,
            detail="Upload the original NASA IMS dataset as a ZIP archive.",
        )
    if not 32 <= window_size <= 4096:
        raise HTTPException(status_code=422, detail="Window size must be between 32 and 4096.")
    max_upload_size_mb = int(
        get_system_setting(db, "max_upload_size_mb", settings.MAX_UPLOAD_SIZE_MB)
    )
    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_path = upload_dir / f"{uuid.uuid4().hex}.zip"
    digest = hashlib.sha256()
    byte_count = 0
    too_large = False
    try:
        with stored_path.open("wb") as target:
            while chunk := file.file.read(1024 * 1024):
                byte_count += len(chunk)
                if byte_count > max_upload_size_mb * 1024 * 1024:
                    too_large = True
                    break
                digest.update(chunk)
                target.write(chunk)
    except OSError as error:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500, detail="The uploaded archive could not be saved."
        ) from error
    if too_large:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {max_upload_size_mb} MB upload limit",
        )
    if not byte_count:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="The uploaded archive is empty.")
    seen_paths: set[Path] = set()
    for existing in db.scalars(select(Dataset).where(Dataset.file_format == "nasa_ims")):
        existing_path = Path(existing.file_path)
        if existing_path.is_file() and existing_path.resolve() not in seen_paths:
            seen_paths.add(existing_path.resolve())
            if file_sha256(existing_path) == digest.hexdigest():
                stored_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=409,
                    detail=f"This NASA IMS archive has already been imported as dataset {existing.id}.",
                )

    try:
        archive_entries(str(stored_path))
    except ValueError as error:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        datasets = persist_nasa_ims_archive(
            db=db,
            archive_path=stored_path,
            source_filename=file.filename or "nasa-ims.zip",
            user_id=user.id,
            window_size=window_size,
            description=description,
        )
        db.commit()
        for dataset in datasets:
            db.refresh(dataset)
    except ValueError as error:
        db.rollback()
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        db.rollback()
        stored_path.unlink(missing_ok=True)
        raise
    return dataset_payload(datasets[0])


def import_job_payload(job: DatasetImportJob | None) -> dict:
    if job is None:
        return {
            "status": "not_started",
            "downloaded_bytes": 0,
            "total_bytes": None,
            "dataset_ids": [],
            "error_message": None,
            "updated_at": None,
        }
    return {
        "status": job.status,
        "downloaded_bytes": job.downloaded_bytes,
        "total_bytes": job.total_bytes,
        "dataset_ids": job.dataset_ids,
        "error_message": job.error_message,
        "updated_at": job.updated_at,
    }


def fail_stale_nasa_ims_import(db: Session, job: DatasetImportJob | None) -> None:
    if job is None or job.status not in _IMPORT_STALE_AFTER:
        return
    updated_at = job.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - updated_at <= _IMPORT_STALE_AFTER[job.status]:
        return
    job.status = "failed"
    job.error_message = (
        "The previous NASA IMS import stopped responding. Retry initialization; "
        "the downloaded archive is retained for reuse."
    )
    job.completed_at = None
    db.commit()
    db.refresh(job)


@router.get("/nasa-ims/import-status")
def get_nasa_ims_import_status(
    _: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    job = db.get(DatasetImportJob, NASA_IMS_SOURCE_KEY)
    fail_stale_nasa_ims_import(db, job)
    return import_job_payload(job)


@router.post("/nasa-ims/initialize", status_code=status.HTTP_202_ACCEPTED)
def initialize_nasa_ims(
    background_tasks: BackgroundTasks,
    device_id: int | None = None,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    if device_id is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                "NASA IMS test sets are separate historical experiments and "
                "cannot be assigned to an equipment device."
            ),
        )
    db.execute(
        postgres_insert(DatasetImportJob)
        .values(
            source_key=NASA_IMS_SOURCE_KEY,
            status="not_started",
            device_id=device_id,
            requested_by=user.id,
            downloaded_bytes=0,
            total_bytes=None,
            dataset_ids=[],
            error_message=None,
        )
        .on_conflict_do_nothing(index_elements=["source_key"])
    )
    job = db.scalar(
        select(DatasetImportJob)
        .where(DatasetImportJob.source_key == NASA_IMS_SOURCE_KEY)
        .with_for_update()
    )
    if job is None:
        raise HTTPException(
            status_code=500, detail="NASA IMS import status could not be initialized."
        )
    fail_stale_nasa_ims_import(db, job)
    if job.status == "completed" and all(
        db.get(Dataset, dataset_id) is not None for dataset_id in job.dataset_ids
    ):
        return import_job_payload(job)
    if job.status in {"queued", "downloading", "extracting", "processing"}:
        return import_job_payload(job)

    job.status = "queued"
    job.device_id = None
    job.requested_by = user.id
    job.downloaded_bytes = 0
    job.total_bytes = None
    job.dataset_ids = []
    job.error_message = None
    job.completed_at = None
    db.commit()
    background_tasks.add_task(run_nasa_ims_initialization)
    return import_job_payload(job)


@router.get("")
def list_datasets(
    search: str | None = None,
    device_id: int | None = None,
    status_filter: str | None = None,
    uploaded_by: int | None = None,
    limit: int = 100,
    offset: int = 0,
    _: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> list[dict]:
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination values")
    query = select(Dataset).order_by(Dataset.uploaded_at.desc())
    if search:
        query = query.where(Dataset.name.ilike(f"%{search.strip()}%"))
    if device_id:
        query = query.where(Dataset.device_id == device_id)
    if status_filter:
        query = query.where(Dataset.status == status_filter)
    if uploaded_by is not None:
        query = query.where(Dataset.uploaded_by == uploaded_by)
    return [
        dataset_payload(dataset)
        for dataset in db.scalars(query.limit(limit).offset(offset))
    ]


@router.get("/{dataset_id}")
def get_dataset(
    dataset_id: int,
    _: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    result = dataset_payload(dataset)
    result["statistics"] = dataset_statistics(dataset_id, _=_, db=db)
    result["analysis_runs"] = [
        {
            "id": run.id,
            "status": run.status,
            "model_name": run.model_config.name if run.model_config else None,
            "anomalies_found": run.anomalies_found,
            "created_at": run.created_at,
        }
        for run in dataset.analysis_runs
    ]
    return result


def _numeric_feature_expression(feature: str):
    return cast(MeasurementRecord.values[feature].as_string(), Float)


@router.get("/{dataset_id}/download")
def download_dataset(
    dataset_id: int,
    _: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> FileResponse:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    path = Path(dataset.file_path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="The preserved source file is unavailable")
    is_nasa_ims = dataset.file_format == "nasa_ims"
    return FileResponse(
        path,
        media_type="application/zip" if is_nasa_ims else "text/csv",
        filename=f"{dataset.name}.zip" if is_nasa_ims else path.name,
    )


@router.get("/{dataset_id}/statistics")
def dataset_statistics(
    dataset_id: int,
    feature: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    _: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    numeric_features = []
    selected_columns = [func.count(MeasurementRecord.id)]
    all_numeric_features = [
        *numeric_analysis_features(dataset.file_format, dataset.column_metadata)
    ]
    if feature and feature not in all_numeric_features:
        raise HTTPException(status_code=422, detail="Choose a numeric dataset feature")
    numeric_names = [feature] if feature else all_numeric_features
    filters = [MeasurementRecord.dataset_id == dataset_id]
    if start_time:
        filters.append(MeasurementRecord.timestamp >= start_time)
    if end_time:
        filters.append(MeasurementRecord.timestamp <= end_time)
    if start_time and end_time and start_time > end_time:
        raise HTTPException(status_code=422, detail="Start time must be before end time")
    for name in numeric_names:
        numeric_features.append(name)
        value = _numeric_feature_expression(name)
        selected_columns.extend(
            (
                func.count(value),
                func.avg(value),
                func.percentile_cont(0.5).within_group(value),
                func.min(value),
                func.max(value),
                func.stddev_pop(value),
            )
        )
    aggregate = db.execute(
        select(*selected_columns).where(*filters)
    ).one()
    record_count = int(aggregate[0] or 0)
    statistics = {}
    cursor = 1
    for name in numeric_features:
        count, mean, median, minimum, maximum, deviation = aggregate[cursor : cursor + 6]
        cursor += 6
        statistics[name] = {
            "count": int(count or 0),
            "mean": float(mean) if mean is not None else None,
            "median": float(median) if median is not None else None,
            "min": float(minimum) if minimum is not None else None,
            "max": float(maximum) if maximum is not None else None,
            "std": float(deviation) if deviation is not None else None,
            "missing": max(0, record_count - int(count or 0)),
            "invalid_numeric": 0,
        }
    invalid_conditions = [
        _numeric_feature_expression(name).is_(None)
        for name in numeric_features
    ]
    invalid_rows = (
        db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        case((or_(*invalid_conditions), 1), else_=0)
                    ),
                    0,
                )
                ).where(*filters)
        )
        if invalid_conditions
        else 0
    )
    return {
        "dataset_id": dataset.id,
        "rows": record_count,
        "columns_count": len(dataset.column_metadata),
        "invalid_rows": int(invalid_rows or 0),
        "columns": statistics,
        "duplicate_rows": None,
        "timestamp_column": "timestamp",
    }


@router.get("/{dataset_id}/exploration")
def explore_dataset(
    dataset_id: int,
    feature: str | None = None,
    features: str | None = None,
    time_column: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    algorithm: str | None = None,
    severity: str | None = None,
    limit: int = 1000,
    _: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    numeric_features = [
        *numeric_analysis_features(dataset.file_format, dataset.column_metadata)
    ]
    selected_feature = feature or (numeric_features[0] if numeric_features else None)
    if selected_feature not in numeric_features:
        raise HTTPException(status_code=422, detail="Choose a numeric dataset feature")
    selected_features = (
        [name.strip() for name in features.split(",") if name.strip()]
        if features
        else [selected_feature]
    )
    if not selected_features or len(selected_features) > 8:
        raise HTTPException(status_code=422, detail="Select between one and eight features")
    invalid_features = [name for name in selected_features if name not in numeric_features]
    if invalid_features:
        raise HTTPException(
            status_code=422,
            detail=f"Features are not available in this dataset: {invalid_features}",
        )
    selected_time_column = time_column or "timestamp"
    if selected_time_column not in {"timestamp"}:
        raise HTTPException(
            status_code=422, detail="Time column is not present in this dataset"
        )
    if not 1 <= limit <= 2000:
        raise HTTPException(status_code=422, detail="Exploration limit must be between 1 and 2000")
    if start_time and end_time and start_time > end_time:
        raise HTTPException(status_code=422, detail="Start time must be before end time")
    if algorithm and algorithm not in {"isolation_forest", "z_score"}:
        raise HTTPException(status_code=422, detail="Unsupported detection algorithm")
    if severity and severity not in {"low", "medium", "high", "critical"}:
        raise HTTPException(status_code=422, detail="Unsupported anomaly severity")

    filters = [MeasurementRecord.dataset_id == dataset_id]
    if start_time:
        filters.append(MeasurementRecord.timestamp >= start_time)
    if end_time:
        filters.append(MeasurementRecord.timestamp <= end_time)
    matching_count = db.scalar(
        select(func.count(MeasurementRecord.id)).where(*filters)
    ) or 0
    stride = max(1, (matching_count + limit - 1) // limit)

    completed_runs = list(
        db.scalars(
            select(AnalysisRun)
            .join(ModelConfig)
            .where(
                AnalysisRun.dataset_id == dataset_id,
                AnalysisRun.status == "completed",
                ModelConfig.algorithm.in_(
                    [algorithm] if algorithm else ["isolation_forest", "z_score"]
                ),
            )
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        )
    )
    latest_run_by_algorithm = {}
    for run in completed_runs:
        latest_run_by_algorithm.setdefault(run.model_config.algorithm, run)
    anomaly_matches = []
    for run in latest_run_by_algorithm.values():
        anomaly_query = (
            select(
                AnalysisResult.source_row_number,
                Anomaly.id,
                Anomaly.severity,
                Anomaly.anomaly_score,
                Anomaly.expected_range,
                Anomaly.description,
            )
            .join(
                MeasurementRecord,
                AnalysisResult.measurement_record_id == MeasurementRecord.id,
            )
            .outerjoin(
                Anomaly,
                (Anomaly.analysis_run_id == AnalysisResult.analysis_run_id)
                & (Anomaly.measurement_record_id == AnalysisResult.measurement_record_id),
            )
            .where(
                AnalysisResult.analysis_run_id == run.id,
                AnalysisResult.is_anomaly.is_(True),
                *(
                    [MeasurementRecord.timestamp >= start_time]
                    if start_time
                    else []
                ),
                *(
                    [MeasurementRecord.timestamp <= end_time]
                    if end_time
                    else []
                ),
                *(
                    [Anomaly.severity == severity]
                    if severity
                    else []
                ),
            )
            .order_by(AnalysisResult.anomaly_score.desc())
            .limit(5000)
        )
        anomaly_matches.extend(
            (
                *row,
                run.id,
                run.model_config.algorithm,
                run.model_config.parameters,
            )
            for row in db.execute(anomaly_query)
        )
    anomalies_by_row = {}
    for (
        source_row,
        anomaly_id,
        anomaly_severity,
        score,
        expected_range,
        description,
        analysis_id,
        algorithm_name,
        parameters,
    ) in anomaly_matches:
        anomalies_by_row.setdefault(source_row, []).append(
            {
                "id": anomaly_id,
                "analysis_id": analysis_id,
                "algorithm": algorithm_name,
                "severity": anomaly_severity or "medium",
                "score": score,
                "threshold": (parameters or {}).get("threshold"),
                "expected_range": expected_range or {},
                "description": description,
            }
        )

    numeric_expressions = [
        _numeric_feature_expression(name).label(name) for name in selected_features
    ]
    measurement_rows = list(
        db.execute(
            select(MeasurementRecord, *numeric_expressions)
            .where(*filters)
            .order_by(MeasurementRecord.source_row_number)
            .where(func.mod(MeasurementRecord.source_row_number - 1, stride) == 0)
            .limit(limit)
        )
    )
    selected_source_rows = {record.source_row_number for record, *_ in measurement_rows}
    missing_anomaly_rows = set(anomalies_by_row) - selected_source_rows
    if missing_anomaly_rows:
        anomaly_measurements = db.execute(
            select(MeasurementRecord, *numeric_expressions)
            .where(
                MeasurementRecord.dataset_id == dataset_id,
                MeasurementRecord.source_row_number.in_(missing_anomaly_rows),
            )
            .order_by(MeasurementRecord.source_row_number)
            .limit(min(500, len(missing_anomaly_rows)))
        ).all()
        measurement_rows.extend(anomaly_measurements)
    series = []
    for record, *values in measurement_rows:
        value_map = {
            name: (float(value) if value is not None else None)
            for name, value in zip(selected_features, values, strict=True)
        }
        series.append(
            {
                "source_row_number": record.source_row_number,
                "timestamp": record.timestamp.isoformat() if record.timestamp else None,
                "value": value_map.get(selected_feature),
                "values": value_map,
                "anomalies": anomalies_by_row.get(record.source_row_number, []),
            }
        )
    selected_values = [
        point["value"] for point in series if point["value"] is not None
    ]
    histogram = []
    if selected_values:
        counts, edges = np.histogram(
            selected_values, bins=min(12, max(1, len(set(selected_values))))
        )
        histogram = [
            {
                "range": f"{edges[index]:.4g}–{edges[index + 1]:.4g}",
                "count": int(counts[index]),
            }
            for index in range(len(counts))
        ]
    return {
        "dataset_id": dataset.id,
        "feature": selected_feature,
        "features": selected_features,
        "units": {
            item["name"]: item.get("unit")
            for item in dataset.column_metadata
            if item["name"] in selected_features
        },
        "time_column": selected_time_column,
        "histogram": histogram,
        "series": series,
        "numeric_features": numeric_features,
        "time_columns": ["timestamp"],
        "total_measurements": matching_count,
        "sampled_measurements": len(series),
        "stride": stride,
        "analysis_runs": [
            {
                "id": run.id,
                "algorithm": run.model_config.algorithm,
                "model_name": run.model_config.name,
            }
            for run in latest_run_by_algorithm.values()
        ],
    }


@router.post("/{dataset_id}/compare", status_code=status.HTTP_202_ACCEPTED)
def start_ims_comparison(
    dataset_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if dataset.file_format != "nasa_ims":
        raise HTTPException(
            status_code=409,
            detail="Comparative analysis is available for NASA IMS datasets.",
        )
    features = numeric_analysis_features(
        dataset.file_format, dataset.column_metadata
    )
    if not features:
        raise HTTPException(
            status_code=409, detail="This NASA IMS dataset has no vibration features."
        )
    previous_runs = list(
        db.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.dataset_id == dataset.id)
            .order_by(AnalysisRun.created_at.desc())
        )
    )
    latest_pair: dict[str, AnalysisRun] = {}
    for run in previous_runs:
        config = run.model_config
        if (
            config
            and config.parameters.get("nasa_ims_comparison")
            and config.algorithm in {"isolation_forest", "z_score"}
            and config.algorithm not in latest_pair
        ):
            latest_pair[config.algorithm] = run
    if any(run.status in {"pending", "running"} for run in latest_pair.values()):
        return {
            "dataset_id": dataset.id,
            "analysis_ids": [run.id for run in latest_pair.values()],
            "status": "running",
        }

    configs = {}
    default_contamination = get_system_setting(
        db, "default_contamination", "auto"
    ) or "auto"
    for algorithm, name, description, parameters in (
        (
            "isolation_forest",
            "NASA IMS Isolation Forest",
            "Automatic Isolation Forest comparison for NASA IMS bearing features.",
            {
                "n_estimators": int(
                    get_system_setting(db, "default_n_estimators", 100)
                ),
                "contamination": default_contamination,
                "features": features,
                "missing_values_strategy": "median",
                "scaling": "standard",
                "random_state": 42,
                "max_samples": "auto",
                "max_features": 1.0,
                "bootstrap": False,
                "n_jobs": -1,
                "nasa_ims_comparison": True,
            },
        ),
        (
            "z_score",
            "NASA IMS Z-score",
            "Z-score comparison for NASA IMS bearing features.",
            {
                "features": features,
                "missing_values_strategy": "median",
                "scaling": "standard",
                "threshold": 3.0,
                "nasa_ims_comparison": True,
            },
        ),
    ):
        config = next(
            (
                item
                for item in db.scalars(
                    select(ModelConfig).where(ModelConfig.algorithm == algorithm)
                )
                if item.parameters.get("nasa_ims_comparison")
            ),
            None,
        )
        if config is None:
            config = ModelConfig(
                name=name,
                algorithm=algorithm,
                description=description,
                parameters=parameters,
                contamination=(
                    float(parameters["contamination"])
                    if parameters["contamination"] != "auto"
                    else None
                )
                if algorithm == "isolation_forest"
                else None,
                created_by=user.id,
            )
            db.add(config)
            db.flush()
        configs[algorithm] = config

    runs = []
    for algorithm in ("isolation_forest", "z_score"):
        run = AnalysisRun(
            dataset_id=dataset.id,
            model_config_id=configs[algorithm].id,
            started_by=user.id,
            status="pending",
            total_records=dataset.row_count,
            anomalies_found=0,
            metrics={
                "configuration": {
                    "features": features,
                    "missing_values_strategy": "median",
                    "scaling": "standard",
                    "nasa_ims_comparison": True,
                }
            },
        )
        db.add(run)
        runs.append(run)
    db.add(
        AuditLog(
            user_id=user.id,
            action="analysis",
            entity_type="dataset",
            entity_id=dataset.id,
            details={"algorithms": ["isolation_forest", "z_score"]},
        )
    )
    db.commit()
    for run in runs:
        background_tasks.add_task(run_analysis, run.id)
    return {
        "dataset_id": dataset.id,
        "analysis_ids": [run.id for run in runs],
        "status": "queued",
    }


@router.get("/{dataset_id}/comparison")
def get_ims_comparison(
    dataset_id: int,
    feature: str | None = None,
    limit: int = 100,
    offset: int = 0,
    _: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if dataset.file_format != "nasa_ims":
        raise HTTPException(
            status_code=409,
            detail="Comparative analysis is available for NASA IMS datasets.",
        )
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination values")
    numeric_features = [
        *numeric_analysis_features(dataset.file_format, dataset.column_metadata)
    ]
    selected_feature = feature or (numeric_features[0] if numeric_features else None)
    if selected_feature not in numeric_features:
        raise HTTPException(status_code=422, detail="Choose a NASA IMS vibration feature.")

    runs = [
        run
        for run in db.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.dataset_id == dataset.id)
            .order_by(AnalysisRun.created_at.desc())
        )
        if run.model_config
        and run.model_config.parameters.get("nasa_ims_comparison")
    ]
    latest_pair: dict[str, AnalysisRun] = {}
    for run in runs:
        algorithm = run.model_config.algorithm
        if algorithm in {"isolation_forest", "z_score"} and algorithm not in latest_pair:
            latest_pair[algorithm] = run
    if not latest_pair:
        return {
            "dataset_id": dataset.id,
            "status": "not_started",
            "feature": selected_feature,
            "statistics": [],
            "series": [],
            "anomalies": [],
            "total_anomalies": 0,
            "unique_affected_measurements": 0,
        }
    algorithms = ("isolation_forest", "z_score")
    ordered_runs = [latest_pair[name] for name in algorithms if name in latest_pair]
    if any(run.status == "failed" for run in ordered_runs):
        comparison_status = "failed"
    elif len(latest_pair) < 2 or any(
        run.status != "completed" for run in ordered_runs
    ):
        comparison_status = "running"
    else:
        comparison_status = "completed"

    statistics = [
        {
            "analysis_id": run.id,
            "algorithm": run.model_config.name,
            "status": run.status,
            "records": run.total_records,
            "anomalies": run.anomalies_found,
            "anomaly_rate": (
                round(run.anomalies_found / run.total_records * 100, 2)
                if run.total_records
                else 0
            ),
            "execution_seconds": run.metrics.get("execution_seconds"),
        }
        for run in ordered_runs
    ]
    series = []
    if comparison_status == "completed":
        step = max(1, dataset.row_count // 500)
        for run in ordered_runs:
            points = db.execute(
                select(AnalysisResult, MeasurementRecord)
                .join(
                    MeasurementRecord,
                    AnalysisResult.measurement_record_id == MeasurementRecord.id,
                )
                .where(
                    AnalysisResult.analysis_run_id == run.id,
                    func.mod(AnalysisResult.source_row_number - 1, step) == 0,
                )
                .order_by(AnalysisResult.source_row_number)
                .limit(500)
            )
            series.append(
                {
                    "algorithm": run.model_config.name,
                    "points": [
                        {
                            "sample": result.source_row_number,
                            "timestamp": (
                                record.timestamp.isoformat() if record.timestamp else None
                            ),
                            "value": result.feature_values.get(selected_feature),
                            "is_anomaly": result.is_anomaly,
                        }
                        for result, record in points
                    ],
                }
            )
    analysis_ids = [run.id for run in ordered_runs]
    anomaly_query = (
        select(Anomaly, MeasurementRecord, AnalysisRun)
        .outerjoin(
            MeasurementRecord,
            Anomaly.measurement_record_id == MeasurementRecord.id,
        )
        .join(AnalysisRun, Anomaly.analysis_run_id == AnalysisRun.id)
        .where(Anomaly.analysis_run_id.in_(analysis_ids))
        .order_by(Anomaly.anomaly_score.desc())
    )
    total_anomalies = db.scalar(
        select(func.count(Anomaly.id)).where(
            Anomaly.analysis_run_id.in_(analysis_ids)
        )
    ) or 0
    unique_affected_measurements = db.scalar(
        select(func.count(func.distinct(Anomaly.measurement_record_id))).where(
            Anomaly.analysis_run_id.in_(analysis_ids),
            Anomaly.measurement_record_id.is_not(None),
        )
    ) or 0
    anomaly_rows = db.execute(anomaly_query.limit(limit).offset(offset))
    anomalies = [
        {
            "id": anomaly.id,
            "analysis_id": run.id,
            "algorithm": run.model_config.name,
            "sample": record.source_row_number if record else None,
            "timestamp": record.timestamp.isoformat()
            if record and record.timestamp
            else None,
            "source_file": record.values.get("source_file") if record else None,
            "window_index": record.values.get("window_index") if record else None,
            "score": anomaly.anomaly_score,
            "severity": anomaly.severity,
            "features": anomaly.feature_values,
            "description": anomaly.description,
        }
        for anomaly, record, run in anomaly_rows
    ]
    return {
        "dataset_id": dataset.id,
        "status": comparison_status,
        "feature": selected_feature,
        "statistics": statistics,
        "series": series,
        "anomalies": anomalies,
        "total_anomalies": total_anomalies,
        "unique_affected_measurements": unique_affected_measurements,
        "limit": limit,
        "offset": offset,
        "errors": [
            {"analysis_id": run.id, "message": run.error_message}
            for run in ordered_runs
            if run.status == "failed"
        ],
    }


@router.patch("/{dataset_id}/archive")
def archive_dataset(
    dataset_id: int,
    user: User = Depends(EDITORS),
    db: Session = Depends(get_db),
) -> dict:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    dataset.status = "archived"
    db.add(AuditLog(user_id=user.id, action="archive", entity_type="dataset", entity_id=dataset.id))
    db.commit()
    db.refresh(dataset)
    return dataset_payload(dataset)