# Operations

## Migrations

Run migrations from `Server`:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Check whether model metadata and migrations agree:

```powershell
.\.venv\Scripts\python.exe -m alembic check
```

Every schema change must use an Alembic revision that preserves existing data.

### Opt-in migration scratch test

Build an empty database, upgrade it to head, and compare ORM metadata with the
resulting schema:

```powershell
$env:IAD_RUN_SCRATCH_MIGRATION_TEST="1"
.\.venv\Scripts\python.exe -m unittest tests.test_scratch_migration
Remove-Item Env:IAD_RUN_SCRATCH_MIGRATION_TEST
```

The test uses the configured PostgreSQL server and creates
`iad_scratch_migration_check`. It drops that scratch database during cleanup and
never operates on the application database.

### Unknown Alembic stamp

An unknown revision in `alembic_version`, such as the missing
`9e2a7c4f1b6d` migration incident, means the stamped revision has no matching
source file. Recognise it by comparing:

```powershell
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m alembic history
```

Do not run `upgrade`, `downgrade`, or `stamp` until the missing source is
recovered and the stamped schema is backed up and inspected. A source revision
must match the database objects already present before it can be restored.

## Backup and restore

Create a PostgreSQL backup:

```powershell
pg_dump -h localhost -U <database-user> -Fc <database> > backup.dump
```

Restore to a prepared maintenance database:

```powershell
pg_restore -h localhost -U <database-user> -d <database> --clean --if-exists backup.dump
```

## Reset an administrator password

From `Server`:

```powershell
.\.venv\Scripts\python.exe -m scripts.reset_admin_password
```

The script asks for an administrator email and then for a new password and confirmation.

## Notification archive and restore

Administrators can preview and execute notification archive or restore operations from **Notifications**. These operations are audit-recorded and should not run concurrently with database maintenance.

## Sensor ingestion limits

The per-device sensor limit defaults to 120 readings per rolling 60-second
window and is configurable with the `sensor_ingestion_limit_per_minute`
setting. The global source-IP request guard defaults to 1200 requests per
minute and is configurable with `SENSOR_INGESTION_GLOBAL_REQUEST_LIMIT_PER_MINUTE`.
Missing or invalid device keys are rejected before database ingestion and are
limited per source IP. Sensor batches are accepted or rejected atomically.

429 responses include `Retry-After`, `RateLimit-Limit`,
`RateLimit-Remaining`, and `RateLimit-Reset`.

## Tests

Backend:

```powershell
cd Server
.\.venv\Scripts\python.exe -m pytest -q
```

Frontend:

```powershell
cd Client
.\node_modules\.bin\tsc.cmd -b
npm.cmd run build
npm.cmd run lint
```

Simulator:

```powershell
cd tools\sensor_simulator
python -m unittest discover -s tests -v
```

## Troubleshooting

### WinError 10013

Another process owns the port or Windows policy blocks the socket. Stop the conflicting process or use an approved alternate development port.

### ERR_CONNECTION_REFUSED

Confirm the API is listening on port 8000 and the frontend API URL matches it. Check the API terminal and local firewall.

### A 500 response appears as a CORS error

The browser can hide the real API failure behind a CORS message. Inspect the FastAPI response and server log, then verify `CORS_ORIGINS`, database availability, and migration state.

### Migrations are missing

Run `alembic upgrade head`, restart FastAPI, and run `alembic check`. Do not reset the database as a migration workaround.
