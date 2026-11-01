import subprocess
import sys
import unittest
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    SERVER_ROOT
    / "app"
    / "shared"
    / "database"
    / "migrations"
    / "versions"
    / "a2b3c4d5e6f7_add_active_notification_list_index.py"
)


class NotificationIndexMetadataTests(unittest.TestCase):
    def test_active_notification_index_metadata_matches_migration(self) -> None:
        migration_sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("user_id, created_at DESC", migration_sql)
        self.assertIn("WHERE archived_at IS NULL", migration_sql)

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=SERVER_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout + result.stderr
        self.assertEqual(0, result.returncode, output)
        self.assertIn("No new upgrade operations detected.", output)


if __name__ == "__main__":
    unittest.main()
