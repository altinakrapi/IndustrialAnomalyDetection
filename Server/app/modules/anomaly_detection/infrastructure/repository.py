from sqlalchemy import and_, exists, not_, or_, select
from sqlalchemy.orm import Session

from app.shared.models import (
    AnalysisRun,
    AnalysisComparison,
    Anomaly,
    Dataset,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    NasaIMSValidation,
    SimulatorEvaluation,
    SimulatorTunedConfig,
)


def registered_device_anomaly_filter(*, include_dataset: bool = False):
    """Return the shared SQL predicate for registered-device anomaly visibility."""
    registered_device = or_(
        exists(
            select(Device.id).where(
                Device.id == AnalysisRun.device_id,
                Device.is_registered.is_(True),
            ).correlate(AnalysisRun)
        ),
        exists(
            select(Dataset.id)
            .join(Device, Device.id == Dataset.device_id)
            .where(
                Dataset.id == AnalysisRun.dataset_id,
                Device.is_registered.is_(True),
            )
            .correlate(AnalysisRun)
        ),
    )
    visibility_filter = registered_device
    if include_dataset:
        visibility_filter = or_(
            registered_device,
            and_(
                AnalysisRun.source_type == "dataset",
                not_(registered_device),
            ),
        )
    return visibility_filter


def operational_equipment_anomaly_filter(
    *,
    include_test_data: bool = False,
    excluded_run_ids: set[int] | None = None,
):
    """Return the shared predicate for live, registered equipment anomalies."""
    source_types = ("manual", "sensor", "simulated") if include_test_data else (
        "manual",
        "sensor",
    )
    filters = [
        Anomaly.archived_at.is_(None),
        Anomaly.measurement_record_id.is_(None),
        Anomaly.equipment_measurement_id.is_not(None),
        AnalysisRun.source_type == "equipment",
        AnalysisRun.execution_type == "measurement",
        AnalysisRun.dataset_id.is_(None),
        AnalysisRun.equipment_measurement_id == Anomaly.equipment_measurement_id,
        AnalysisRun.device.has(
            and_(
                Device.is_registered.is_(True),
                Device.status != "archived",
            )
        ),
        AnalysisRun.equipment_measurement.has(
            and_(
                EquipmentMeasurement.source_type.in_(source_types),
                EquipmentMeasurement.parameter.has(
                    EquipmentParameter.device_id == AnalysisRun.device_id
                ),
            )
        ),
    ]
    if excluded_run_ids:
        filters.append(AnalysisRun.id.not_in(excluded_run_ids))
    return tuple(filters)


def excluded_non_operational_analysis_run_ids(db: Session) -> set[int]:
    excluded: set[int] = set()
    purpose_run_lists = (
        SimulatorEvaluation.analysis_run_ids,
        SimulatorTunedConfig.development_run_ids,
        AnalysisComparison.analysis_ids,
        NasaIMSValidation.analysis_run_ids,
    )
    for column in purpose_run_lists:
        for run_ids in db.scalars(select(column)):
            if isinstance(run_ids, list):
                for run_id in run_ids:
                    if isinstance(run_id, int) and not isinstance(run_id, bool):
                        excluded.add(run_id)
                    elif isinstance(run_id, str) and run_id.isdecimal():
                        excluded.add(int(run_id))
    return excluded


def device_anomalies_query(*, include_dataset: bool = False):
    """Return a base anomaly query scoped to registered devices by default."""
    return (
        select(Anomaly)
        .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
        .where(
            registered_device_anomaly_filter(include_dataset=include_dataset)
        )
    )
