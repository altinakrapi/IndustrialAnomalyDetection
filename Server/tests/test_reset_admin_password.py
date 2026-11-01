import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.security import verify_password
from app.shared.database.base import Base
from app.shared.models import AuditLog, User
from scripts.reset_admin_password import reset_administrator_password


class ResetAdministratorPasswordTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_admin_password_reset_is_active_hashed_and_audited(self):
        user = User(
            full_name="Administrator",
            email="admin@example.test",
            password_hash="old",
            role="administrator",
            is_active=False,
        )
        self.db.add(user)
        self.db.commit()

        reset_administrator_password(self.db, "ADMIN@EXAMPLE.TEST", "New-Secret-123")

        self.db.refresh(user)
        self.assertTrue(user.is_active)
        self.assertNotEqual(user.password_hash, "old")
        self.assertTrue(verify_password("New-Secret-123", user.password_hash))
        audit = self.db.scalar(
            select(AuditLog).where(AuditLog.entity_id == user.id)
        )
        self.assertIsNotNone(audit)
        self.assertEqual(audit.action, "reset_password")

    def test_non_admin_is_refused(self):
        user = User(
            full_name="Analyst",
            email="analyst@example.test",
            password_hash="old",
            role="data_analyst",
        )
        self.db.add(user)
        self.db.commit()

        with self.assertRaises(PermissionError):
            reset_administrator_password(self.db, user.email, "New-Secret-123")
        self.db.refresh(user)
        self.assertEqual(user.password_hash, "old")

    def test_short_password_is_refused(self):
        user = User(
            full_name="Administrator",
            email="admin@example.test",
            password_hash="old",
            role="administrator",
        )
        self.db.add(user)
        self.db.commit()

        with self.assertRaises(ValueError):
            reset_administrator_password(self.db, user.email, "short")

    def test_unknown_account_is_refused(self):
        with self.assertRaises(LookupError):
            reset_administrator_password(
                self.db, "missing@example.test", "New-Secret-123"
            )
