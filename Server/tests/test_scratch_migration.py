import os
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine, text


SERVER_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_DATABASE_NAME = "iad_scratch_migration_check"


@unittest.skipUnless(
    os.getenv("IAD_RUN_SCRATCH_MIGRATION_TEST") == "1",
    "set IAD_RUN_SCRATCH_MIGRATION_TEST=1 to build and check the migration scratch database",
)
class ScratchMigrationTests(unittest.TestCase):
    def test_upgrade_and_check_from_empty_database(self) -> None:
        from app.core.config import settings

        configured_url = str(settings.DATABASE_URL)
        parts = urlsplit(configured_url)
        scratch_url = urlunsplit(parts._replace(path=f"/{SCRATCH_DATABASE_NAME}"))
        admin_url = urlunsplit(parts._replace(path="/postgres"))

        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                    ),
                    {"database_name": SCRATCH_DATABASE_NAME},
                )
                connection.execute(
                    text("DROP DATABASE IF EXISTS " + SCRATCH_DATABASE_NAME)
                )
                connection.execute(text("CREATE DATABASE " + SCRATCH_DATABASE_NAME))
        finally:
            admin_engine.dispose()

        environment = os.environ.copy()
        environment["DATABASE_URL"] = scratch_url
        try:
            upgrade = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=SERVER_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, upgrade.returncode, upgrade.stdout + upgrade.stderr)

            check = subprocess.run(
                [sys.executable, "-m", "alembic", "check"],
                cwd=SERVER_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            output = check.stdout + check.stderr
            self.assertEqual(0, check.returncode, output)
            self.assertIn("No new upgrade operations detected.", output)
        finally:
            admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
            try:
                with admin_engine.connect() as connection:
                    connection.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                        ),
                        {"database_name": SCRATCH_DATABASE_NAME},
                    )
                    connection.execute(
                        text("DROP DATABASE IF EXISTS " + SCRATCH_DATABASE_NAME)
                    )
            finally:
                admin_engine.dispose()
