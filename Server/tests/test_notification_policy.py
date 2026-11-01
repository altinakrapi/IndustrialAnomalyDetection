import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import HTTPException

from sqlalchemy import select

from app.core.notifications import (
    notify_user,
    notify_user_rate_limited,
    notify_users_batch,
)
from app.modules.notifications.api.router import (
    ADMIN,
    list_notifications,
    NotificationArchiveInput,
    NotificationRestoreInput,
    archive_notifications,
    archive_notifications_dry_run,
    restore_notifications,
    restore_notifications_dry_run,
    unread_notification_count,
)


class NotificationRateGuardTests(unittest.TestCase):
    def test_archive_rbac_rejects_non_administrators(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            ADMIN(user=SimpleNamespace(role="data_analyst"))

        self.assertEqual(raised.exception.status_code, 403)

    def test_device_rate_guard_suppresses_after_limit(self) -> None:
        db = unittest.mock.Mock()
        db.scalar.side_effect = [20, None]

        with patch("app.core.notifications.notify_user") as notify:
            allowed = notify_user_rate_limited(
                db,
                user_id=7,
                device_id=4,
                limit=20,
                title="Anomaly",
                message="Detected",
                notification_type="anomaly_detected",
                details={"source_type": "sensor"},
                entity_id=11,
                dedupe_key="alert-episode:11",
            )

        self.assertFalse(allowed)
        notify.assert_not_called()
        db.add.assert_called_once()

    def test_device_rate_guard_allows_new_notification_below_limit(self) -> None:
        db = unittest.mock.Mock()
        db.scalar.return_value = 19

        with patch("app.core.notifications.notify_user"):
            allowed = notify_user_rate_limited(
                db,
                user_id=7,
                device_id=4,
                limit=20,
                title="Anomaly",
                message="Detected",
                notification_type="anomaly_detected",
                details={"source_type": "sensor"},
                entity_id=12,
                dedupe_key="alert-episode:12",
            )

        self.assertTrue(allowed)

    def test_device_rate_guard_creates_single_aggregate_notification(self) -> None:
        db = unittest.mock.Mock()
        aggregate = SimpleNamespace(
            details={"suppressed_count": 1},
            message="",
        )
        db.scalar.side_effect = [20, aggregate]

        with patch("app.core.notifications.notify_user") as notify:
            allowed = notify_user_rate_limited(
                db,
                user_id=7,
                device_id=4,
                limit=20,
                title="Anomaly",
                message="Detected",
                notification_type="anomaly_detected",
                details={"source_type": "sensor"},
                dedupe_key="alert-episode:11",
            )

        self.assertFalse(allowed)
        self.assertEqual(aggregate.details["suppressed_count"], 2)
        notify.assert_not_called()

    def test_dataset_summary_is_idempotent_per_recipient(self) -> None:
        db = unittest.mock.Mock()
        with patch("app.core.notifications.postgres_insert") as insert:
            notify_users_batch(
                db,
                [
                    {
                        "user_id": 7,
                        "title": "Analysis completed",
                        "notification_type": "analysis_completed",
                        "details": {"filtered_anomaly_list": "/anomalies?analysis_run_id=12"},
                        "dedupe_key": "analysis-summary:12:7",
                    },
                    {
                        "user_id": 8,
                        "title": "Analysis completed",
                        "notification_type": "analysis_completed",
                        "details": {"filtered_anomaly_list": "/anomalies?analysis_run_id=12"},
                        "dedupe_key": "analysis-summary:12:8",
                    },
                ],
            )
            notify_users_batch(
                db,
                [
                    {
                        "user_id": 7,
                        "title": "Analysis completed",
                        "notification_type": "analysis_completed",
                        "details": {"filtered_anomaly_list": "/anomalies?analysis_run_id=12"},
                        "dedupe_key": "analysis-summary:12:7",
                    }
                ],
            )

        insert.assert_called()
        values_calls = [
            call.kwargs["details"] for call in db.execute.call_args_list if "details" in call.kwargs
        ]
        self.assertEqual(db.execute.call_count, 2)

    def test_archive_dry_run_and_archive_are_distinct(self) -> None:
        db = unittest.mock.Mock()
        db.scalar.return_value = 397008
        payload = NotificationArchiveInput(
            before=datetime(2027, 1, 1, tzinfo=timezone.utc),
            notification_type="anomaly_detected",
            related_entity_type="anomaly",
            analysis_run_id=None,
        )

        dry_run = archive_notifications_dry_run(payload=payload, _=SimpleNamespace(), db=db)
        self.assertEqual(dry_run["count"], 397008)

        db.scalars.return_value = iter([101, 102])
        archived = archive_notifications(
            payload=payload,
            user=SimpleNamespace(id=1),
            db=db,
        )
        self.assertEqual(archived["archived"], 2)
        db.add.assert_called_once()

    def test_restore_dry_run_and_restore_are_admin_audited(self) -> None:
        db = unittest.mock.Mock()
        db.scalar.return_value = 12
        payload = NotificationRestoreInput(
            archived_before=datetime(2027, 1, 1, tzinfo=timezone.utc),
            notification_type="anomaly_detected",
        )

        dry_run = restore_notifications_dry_run(
            payload=payload,
            user=SimpleNamespace(id=1),
            db=db,
        )
        self.assertEqual(dry_run["count"], 12)
        self.assertTrue(db.add.called)

        db.scalars.return_value = iter([101, 102])
        restored = restore_notifications(
            payload=payload,
            user=SimpleNamespace(id=1),
            db=db,
        )
        self.assertEqual(restored["restored"], 2)

    def test_dataset_summary_count_and_filtered_link_are_per_recipient(self) -> None:
        db = unittest.mock.Mock()
        db.execute.return_value = None

        notify_users_batch(
            db,
            [
                {
                    "user_id": 7,
                    "title": "Dataset analysis completed",
                    "message": "3 anomalies found",
                    "notification_type": "analysis_completed",
                    "details": {
                        "anomaly_count": 3,
                        "filtered_anomaly_list": "/anomalies?analysis_run_id=12",
                    },
                    "dedupe_key": "analysis-summary:12:7",
                }
            ],
        )

        values = db.execute.call_args.args[0].compile().params
        self.assertEqual(values["user_id_m0"], 7)
        self.assertEqual(values["details_m0"]["anomaly_count"], 3)
        self.assertEqual(
            values["details_m0"]["filtered_anomaly_list"],
            "/anomalies?analysis_run_id=12",
        )

    def test_notification_pagination_and_unread_count(self) -> None:
        db = unittest.mock.Mock()
        db.scalar.return_value = 4
        notifications = [
            SimpleNamespace(
                id=1,
                title="Anomaly",
                message="Detected",
                notification_type="anomaly_detected",
                details={},
                is_read=False,
                related_entity_type="anomaly",
                related_entity_id=9,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                id=2,
                title="Anomaly",
                message="Detected",
                notification_type="anomaly_detected",
                details={},
                is_read=True,
                related_entity_type="anomaly",
                related_entity_id=10,
                created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            ),
        ]
        db.scalars.return_value = iter(notifications)

        result = list_notifications(limit=2, offset=0, user=SimpleNamespace(id=7), db=db)
        unread = unread_notification_count(user=SimpleNamespace(id=7), db=db)

        self.assertEqual(len(result), 2)
        self.assertEqual(unread["unread_count"], 4)
        query = db.scalars.call_args.args[0]
        self.assertIn("LIMIT", str(query.compile()).upper())
        self.assertIn("OFFSET", str(query.compile()).upper())

    def test_archive_keeps_rows_and_audit(self) -> None:
        db = unittest.mock.Mock()
        db.scalars.return_value = iter([101, 102])
        payload = NotificationArchiveInput(
            before=datetime(2027, 1, 1, tzinfo=timezone.utc),
            notification_type="anomaly_detected",
            related_entity_type="anomaly",
            analysis_run_id=12,
        )

        archived = archive_notifications(
            payload=payload,
            user=SimpleNamespace(id=1),
            db=db,
        )

        self.assertEqual(archived["archived"], 2)
        audit = db.add.call_args.args[0]
        self.assertEqual(audit.action, "archive")
        self.assertEqual(audit.details["count"], 2)
        db.commit.assert_called_once()
        db.execute.assert_called_once()
