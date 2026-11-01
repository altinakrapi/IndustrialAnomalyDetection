from sqlalchemy import and_, exists, not_, or_, select

from app.shared.models import AnalysisRun, Anomaly, Dataset, Device


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


def device_anomalies_query(*, include_dataset: bool = False):
    """Return a base anomaly query scoped to registered devices by default."""
    return (
        select(Anomaly)
        .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
        .where(
            registered_device_anomaly_filter(include_dataset=include_dataset)
        )
    )
