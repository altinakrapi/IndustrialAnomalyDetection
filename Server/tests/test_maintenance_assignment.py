import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException

from app.modules.maintenance.api.router import (
    ActionInput,
    MaintenanceUpdate,
    create_action,
    update_order,
)


class MaintenanceAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Mock()
        self.db.get.return_value = SimpleNamespace(
            id=4,
            assigned_to=12,
            status="assigned",
        )
        self.unassigned_technician = SimpleNamespace(
            id=13,
            role="maintenance_technician",
        )

    def test_only_assigned_technician_can_update_order(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            update_order(
                4,
                MaintenanceUpdate(status="in_progress"),
                user=self.unassigned_technician,
                db=self.db,
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.db.commit.assert_not_called()

    def test_only_assigned_technician_can_record_inspection_findings(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            create_action(
                4,
                ActionInput(action_description="Inspected the bearing"),
                user=self.unassigned_technician,
                db=self.db,
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
