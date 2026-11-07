from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.modules.anomaly_detection.infrastructure.repository import (
    excluded_non_operational_analysis_run_ids,
    operational_equipment_anomaly_filter,
)
from app.shared.models import (
    AlertEpisode,
    AnalysisRun,
    Anomaly,
    Device,
    EquipmentMeasurement,
    EquipmentParameter,
    NasaIMSValidation,
)


def dry_run_live_episode_link_candidates(db: Session) -> dict[str, int | bool]:
    excluded_run_ids = excluded_non_operational_analysis_run_ids(db)
    candidates = (
        select(
            Anomaly.id.label("anomaly_id"),
            AnalysisRun.device_id.label("device_id"),
            EquipmentMeasurement.parameter_id.label("parameter_id"),
            Anomaly.detected_at.label("detected_at"),
        )
        .join(AnalysisRun, AnalysisRun.id == Anomaly.analysis_run_id)
        .join(
            EquipmentMeasurement,
            EquipmentMeasurement.id == Anomaly.equipment_measurement_id,
        )
        .join(
            EquipmentParameter,
            EquipmentParameter.id == EquipmentMeasurement.parameter_id,
        )
        .join(Device, Device.id == AnalysisRun.device_id)
        .where(
            *operational_equipment_anomaly_filter(
                include_test_data=True,
                excluded_run_ids=excluded_run_ids,
            ),
            Anomaly.alert_episode_id.is_(None),
        )
        .subquery()
    )
    match_counts = db.execute(
        select(
            candidates.c.anomaly_id,
            func.count(AlertEpisode.id).label("match_count"),
        )
        .select_from(candidates)
        .outerjoin(
            AlertEpisode,
            and_(
                AlertEpisode.device_id == candidates.c.device_id,
                AlertEpisode.parameter_id == candidates.c.parameter_id,
                candidates.c.detected_at.between(
                    AlertEpisode.first_detected_at,
                    AlertEpisode.last_detected_at,
                ),
            ),
        )
        .group_by(candidates.c.anomaly_id)
    ).all()
    one_match = sum(row.match_count == 1 for row in match_counts)
    ambiguous = sum(row.match_count > 1 for row in match_counts)
    return {
        "dry_run_only": True,
        "eligible_live_rows": len(match_counts),
        "one_episode_match": one_match,
        "ambiguous_episode_matches": ambiguous,
        "no_episode_match": len(match_counts) - one_match - ambiguous,
        "writes_performed": 0,
    }
