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
