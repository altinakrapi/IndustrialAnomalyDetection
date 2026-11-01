import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.notifications import notify_roles, notify_user
from app.modules.devices.application.schedule import measurement_schedule
from app.modules.devices.application.stale_data import (
    TELEMETRY_SOURCE_TYPES,
    evaluate_stale_reporting,
)
from app.shared.database.session import SessionLocal, engine
from app.shared.feature_selection import numeric_analysis_features
from app.shared.models import (
    AnalysisRun,
    Dataset,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    ModelConfig,
    MonitoringSchedule,
)
from app.workers.alert_escalation import escalate_unreviewed_alerts
from app.workers.analysis_worker import run_analysis

logger = logging.getLogger(__name__)
_ADVISORY_LOCK_ID = 1_229_790_277
_STALE_RUN_AFTER = timedelta(minutes=30)
_ALGORITHMS = ("isolation_forest", "z_score")
_AUTOMATIC_MODEL_NAMES = {
    "isolation_forest": "Automatic NASA IMS Isolation Forest",
    "z_score": "Automatic NASA IMS Z-score",
}


def _analysis_features(dataset: Dataset, config: ModelConfig) -> list[str]:
    available = numeric_analysis_features(
        dataset.file_format, dataset.column_metadata
    )
    configured = config.parameters.get("features", [])
    if configured:
        invalid_features = [feature for feature in configured if feature not in available]
        if invalid_features:
            raise ValueError(
                f"Configured features are not valid measurement features: {invalid_features}"
            )
        return list(configured)
    return available


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _ensure_schedule(db: Session) -> MonitoringSchedule:
    schedule = db.get(MonitoringSchedule, 1)
    if schedule is None:
        schedule = MonitoringSchedule(
            id=1,
            enabled=True,
            interval_minutes=60,
            simulation_enabled=False,
            simulation_batch_size=100,
            next_run_at=datetime.now(timezone.utc),
        )
        db.add(schedule)
        db.flush()
    return schedule


def _ensure_models(db: Session) -> dict[str, ModelConfig]:
    configs = list(
        db.scalars(
            select(ModelConfig)
            .where(ModelConfig.name.in_(_AUTOMATIC_MODEL_NAMES.values()))
            .order_by(ModelConfig.created_at.desc())
        )
    )
    selected: dict[str, ModelConfig] = {}
    for config in configs:
        algorithm = config.algorithm.lower().replace("-", "_").replace(" ", "_")
        if (
            algorithm in _ALGORITHMS
            and config.name == _AUTOMATIC_MODEL_NAMES[algorithm]
        ):
            selected.setdefault(algorithm, config)
            config.is_active = True

    for algorithm in _ALGORITHMS:
        if algorithm in selected:
            continue
        is_forest = algorithm == "isolation_forest"
        config = ModelConfig(
            name=_AUTOMATIC_MODEL_NAMES[algorithm],
            algorithm=algorithm,
            description=(
                "Automatically maintained model configuration for scheduled "
                "NASA IMS historical dataset analysis."
            ),
            parameters={
                "n_estimators": 100,
                "contamination": "auto",
                "features": [],
                "missing_values_strategy": "median",
                "scaling": "standard",
                "threshold": 3.0,
                "random_state": 42,
                "max_samples": "auto",
                "max_features": 1.0,
                "bootstrap": False,
                "n_jobs": -1,
            },
            contamination=None,
            created_by=None,
        )
        db.add(config)
        db.flush()
        selected[algorithm] = config
    return selected


def _completed_non_simulated_run(
    db: Session,
    dataset: Dataset,
    config: ModelConfig,
    execution_key: str,
) -> bool:
    active_runs = list(
        db.scalars(
            select(AnalysisRun)
            .where(
                AnalysisRun.dataset_id == dataset.id,
                AnalysisRun.model_config_id == config.id,
                AnalysisRun.execution_type != "simulated",
                AnalysisRun.status.in_(("pending", "running")),
            )
        )
    )
    now = datetime.now(timezone.utc)
    for active_run in active_runs:
        last_activity = _utc(active_run.started_at or active_run.created_at)
        if last_activity and now - last_activity < _STALE_RUN_AFTER:
            return True
        active_run.status = "failed"
        active_run.error_message = (
            "The previous analysis worker stopped before completion."
        )
        active_run.completed_at = now

    completed_run = db.scalar(
        select(AnalysisRun.id)
        .where(
            AnalysisRun.dataset_id == dataset.id,
            AnalysisRun.model_config_id == config.id,
            AnalysisRun.execution_type == "scheduled",
            AnalysisRun.execution_key == execution_key,
            AnalysisRun.status == "completed",
        )
        .limit(1)
    )
    if completed_run is not None:
        return True

    legacy_key = f"initial:{dataset.id}:{config.id}"
    legacy_completed_run = db.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.dataset_id == dataset.id,
            AnalysisRun.model_config_id == config.id,
            AnalysisRun.execution_type == "scheduled",
            AnalysisRun.execution_key == legacy_key,
            AnalysisRun.status == "completed",
            AnalysisRun.total_records > 0,
            AnalysisRun.source_start_row.is_(None),
            AnalysisRun.source_end_row.is_(None),
        )
        .limit(1)
    )
    if legacy_completed_run is None:
        return False

    legacy_completed_run.execution_key = execution_key
    return True


def _scheduled_execution_key(dataset: Dataset, config: ModelConfig) -> str:
    snapshot = json.dumps(
        {
            "dataset_id": dataset.id,
            "row_count": dataset.row_count,
            "uploaded_at": dataset.uploaded_at.isoformat(),
            "file_path": dataset.file_path,
            "column_metadata": dataset.column_metadata,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    fingerprint = hashlib.sha256(snapshot).hexdigest()
    return f"scheduled:{dataset.id}:{fingerprint}:{config.id}"


def _prepare_run(
    db: Session,
    *,
    dataset: Dataset,
    config: ModelConfig,
    execution_type: str,
    execution_key: str,
    start_row: int | None = None,
    end_row: int | None = None,
) -> int | None:
    features = _analysis_features(dataset, config)
    run = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.execution_key == execution_key)
        .with_for_update()
    )
    if run is not None:
        if run.status == "completed":
            return None
        if run.status == "running":
            last_activity = _utc(run.started_at or run.created_at)
            if last_activity and datetime.now(timezone.utc) - last_activity < _STALE_RUN_AFTER:
                return None
            run.status = "failed"
            run.error_message = "The previous analysis worker stopped before completion."
        if run.status == "pending":
            last_activity = _utc(run.created_at)
            if last_activity and datetime.now(timezone.utc) - last_activity < _STALE_RUN_AFTER:
                return run.id
            run.status = "failed"
            run.error_message = "The previous analysis worker did not start."
        run.status = "pending"
        run.started_at = None
        run.completed_at = None
        run.metrics = {
            "configuration": {
                "model_config_id": config.id,
                "model_name": config.name,
                "algorithm": config.algorithm,
                "parameters": config.parameters,
                "features": features,
                "missing_values_strategy": config.parameters.get(
                    "missing_values_strategy", "median"
                ),
                "scaling": config.parameters.get("scaling", "standard"),
            }
        }
        run.error_message = None
        return run.id

    run_configuration = {
        "model_config_id": config.id,
        "model_name": config.name,
        "algorithm": config.algorithm,
        "parameters": config.parameters,
        "features": features,
        "missing_values_strategy": config.parameters.get(
            "missing_values_strategy", "median"
        ),
        "scaling": config.parameters.get("scaling", "standard"),
    }
    if start_row is not None:
        run_configuration["source_row_start"] = start_row
        run_configuration["source_row_end"] = end_row
    run = AnalysisRun(
        dataset_id=dataset.id,
        model_config_id=config.id,
        started_by=None,
        status="pending",
        execution_type=execution_type,
        execution_key=execution_key,
        source_start_row=start_row,
        source_end_row=end_row,
        metrics={"configuration": run_configuration},
    )
    db.add(run)
    db.flush()
    return run.id


def _next_simulation_range(
    db: Session, dataset_id: int, model_config_id: int, row_count: int, batch_size: int
) -> tuple[int, int] | None:
    last_row = db.scalar(
        select(func.max(AnalysisRun.source_end_row)).where(
            AnalysisRun.dataset_id == dataset_id,
            AnalysisRun.model_config_id == model_config_id,
            AnalysisRun.execution_type == "simulated",
            AnalysisRun.status == "completed",
        )
    ) or 0
    start_row = last_row + 1
    if start_row >= row_count:
        return None
    end_row = min(row_count, start_row + batch_size - 1)
    if end_row - start_row < 1:
        return None
    return start_row, end_row


def _notify_overdue_equipment_measurements(db: Session, now: datetime) -> None:
    parameters = list(
        db.scalars(
            select(EquipmentParameter)
            .join(Device, EquipmentParameter.device_id == Device.id)
            .where(
                EquipmentParameter.enabled.is_(True),
                Device.is_registered.is_(True),
                Device.status == "active",
            )
        )
    )
    for parameter in parameters:
        latest_measurement = db.scalar(
            select(EquipmentMeasurement)
            .where(EquipmentMeasurement.parameter_id == parameter.id)
            .order_by(
                EquipmentMeasurement.received_at.desc(),
                EquipmentMeasurement.id.desc(),
            )
            .limit(1)
        )
        if (
            parameter.device.source_type in TELEMETRY_SOURCE_TYPES
            or (
                latest_measurement is not None
                and latest_measurement.source_type in TELEMETRY_SOURCE_TYPES
            )
        ):
            evaluate_stale_reporting(
                db,
                parameter,
                latest_measurement,
                now,
            )
            continue
        due_at, measurement_status = measurement_schedule(
            parameter,
            latest_measurement.measured_at if latest_measurement else None,
            now,
        )
        if measurement_status != "overdue":
            continue
        overdue_minutes = int((now - due_at).total_seconds() // 60)
        notify_roles(
            db,
            {"administrator", "data_analyst", "maintenance_technician"},
            "Equipment measurement overdue",
            (
                f"{parameter.device.name} · {parameter.name} has no new measurement "
                f"within its {parameter.expected_interval_minutes}-minute interval "
                f"and is {overdue_minutes} minutes overdue."
            ),
            "measurement_overdue",
            "equipment_parameter",
            parameter.id,
            details={
                "device_id": parameter.device_id,
                "parameter_id": parameter.id,
                "due_at": due_at.isoformat(),
                "overdue_minutes": overdue_minutes,
            },
            dedupe_key=(
                f"measurement-overdue:{parameter.id}:{due_at.isoformat()}"
            ),
        )
        notify_user(
            db,
            getattr(parameter.device, "responsible_user_id", None),
            "Equipment measurement overdue",
            (
                f"{parameter.device.name} · {parameter.name} needs an actual "
                f"manual measurement; it is {overdue_minutes} minutes overdue."
            ),
            "measurement_overdue",
            "equipment_parameter",
            parameter.id,
            details={
                "device_id": parameter.device_id,
                "parameter_id": parameter.id,
                "due_at": due_at.isoformat(),
                "overdue_minutes": overdue_minutes,
            },
            dedupe_key=(
                f"measurement-overdue:{parameter.id}:{due_at.isoformat()}"
            ),
        )


def _schedule_due_cycle() -> None:
    run_ids: list[int] = []
    with SessionLocal.begin() as db:
        schedule = db.scalar(
            select(MonitoringSchedule)
            .where(MonitoringSchedule.id == 1)
            .with_for_update()
        )
        now = datetime.now(timezone.utc)
        _notify_overdue_equipment_measurements(db, now)
        if schedule is None or not schedule.enabled:
            return
        next_run_at = _utc(schedule.next_run_at)
        if next_run_at is not None and next_run_at > now:
            return

        schedule.last_started_at = now
        schedule.next_run_at = now + timedelta(minutes=schedule.interval_minutes)
        schedule.last_error = None
        configs = _ensure_models(db)
        datasets = list(
            db.scalars(
                select(Dataset)
                .where(
                    Dataset.file_format == "nasa_ims",
                    Dataset.status != "archived",
                    Dataset.row_count >= 2,
                )
                .order_by(Dataset.id)
            )
        )

        for dataset in datasets:
            for config in configs.values():
                if schedule.simulation_enabled:
                    row_range = _next_simulation_range(
                        db,
                        dataset.id,
                        config.id,
                        dataset.row_count,
                        schedule.simulation_batch_size,
                    )
                    if row_range is None:
                        continue
                    start_row, end_row = row_range
                    run_id = _prepare_run(
                        db,
                        dataset=dataset,
                        config=config,
                        execution_type="simulated",
                        execution_key=(
                            f"sim:{dataset.id}:{config.id}:{start_row}-{end_row}"
                        ),
                        start_row=start_row,
                        end_row=end_row,
                    )
                else:
                    execution_key = _scheduled_execution_key(dataset, config)
                    if _completed_non_simulated_run(
                        db, dataset, config, execution_key
                    ):
                        continue
                    run_id = _prepare_run(
                        db,
                        dataset=dataset,
                        config=config,
                        execution_type="scheduled",
                        execution_key=execution_key,
                    )
                if run_id is not None:
                    run_ids.append(run_id)

    for run_id in run_ids:
        run_analysis(run_id)

    with SessionLocal.begin() as db:
        schedule = db.get(MonitoringSchedule, 1)
        if schedule is not None:
            failed = list(
                db.scalars(
                    select(AnalysisRun)
                    .where(
                        AnalysisRun.id.in_(run_ids),
                        AnalysisRun.status == "failed",
                    )
                    .order_by(AnalysisRun.id)
                )
            ) if run_ids else []
            schedule.last_completed_at = datetime.now(timezone.utc)
            schedule.last_error = (
                "; ".join(
                    f"Analysis #{run.id}: {run.error_message or 'failed'}"
                    for run in failed
                )[:2000]
                or None
            )


def run_due_cycle() -> None:
    with engine.connect() as lock_connection:
        acquired = lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": _ADVISORY_LOCK_ID},
        )
        lock_connection.commit()
        if not acquired:
            return
        try:
            _schedule_due_cycle()
        except Exception as error:
            logger.exception("Scheduled monitoring cycle failed")
            try:
                with SessionLocal.begin() as db:
                    schedule = db.get(MonitoringSchedule, 1)
                    if schedule is not None:
                        schedule.last_error = str(error)[:2000]
                        schedule.last_completed_at = datetime.now(timezone.utc)
                        if schedule.enabled:
                            schedule.next_run_at = datetime.now(timezone.utc) + timedelta(
                                minutes=schedule.interval_minutes
                            )
            except Exception:
                logger.exception("Could not persist scheduled monitoring failure")
        try:
            with SessionLocal.begin() as db:
                escalate_unreviewed_alerts(db)
        except Exception:
            logger.exception("Scheduled alert escalation cycle failed")
        finally:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": _ADVISORY_LOCK_ID},
            )
            lock_connection.commit()


async def monitoring_loop() -> None:
    import asyncio

    while True:
        try:
            await asyncio.to_thread(run_due_cycle)
        except Exception:
            logger.exception("Scheduled monitoring poll failed")
        await asyncio.sleep(5)
