from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.notifications import notify_roles
from app.modules.devices.application.alerting import escalation_cutoff
from app.shared.models import AlertEpisode, AuditLog


def escalate_unreviewed_alerts(
    db: Session, now: datetime | None = None
) -> int:
    current_time = now or datetime.now(timezone.utc)
    cutoff = escalation_cutoff(db, current_time)
    episodes = list(
        db.scalars(
            select(AlertEpisode)
            .where(
                AlertEpisode.status == "open",
                AlertEpisode.reviewed_at.is_(None),
                AlertEpisode.escalated_at.is_(None),
                AlertEpisode.first_detected_at <= cutoff,
            )
            .order_by(AlertEpisode.first_detected_at, AlertEpisode.id)
            .with_for_update(skip_locked=True)
        )
    )
    for episode in episodes:
        episode.priority = "escalated"
        episode.escalated_at = current_time
        details = {
            "alert_episode_id": episode.id,
            "device_id": episode.device_id,
            "parameter_id": episode.parameter_id,
            "measurement_id": episode.last_measurement_id,
            "alert_rules": episode.alert_rules,
            "alert_priority": episode.priority,
            "severity": episode.severity,
            "history_size": episode.history_size,
            "history_required": episode.history_required,
        }
        notify_roles(
            db,
            {"administrator"},
            "Unreviewed alert escalated",
            (
                f"Alert episode #{episode.id} has remained unreviewed beyond "
                "the configured review interval. The readings need attention."
            ),
            "alert_escalated",
            "alert_episode",
            episode.id,
            details=details,
            dedupe_key=f"alert-escalation:{episode.id}",
        )
        db.add(
            AuditLog(
                user_id=None,
                action="escalate",
                entity_type="alert_episode",
                entity_id=episode.id,
                details=details,
            )
        )
    return len(episodes)
