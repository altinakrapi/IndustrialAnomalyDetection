# Industrial Anomaly Detection API

This directory contains the FastAPI backend, PostgreSQL models, Alembic migrations, workers, and backend tests.

## Start the API

From `Server`, using the repository virtual environment:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

API documentation is available at `http://localhost:8000/docs`.

## Backend layout

- `app/modules/<feature>/api`: FastAPI routers and request parsing.
- `app/modules/<feature>/application`: use cases and orchestration.
- `app/modules/<feature>/domain`: pure feature rules.
- `app/modules/<feature>/infrastructure`: persistence adapters.
- `app/modules/<feature>/schemas`: API request and response models.
- `app/shared/models`: shared SQLAlchemy models.

Run backend tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

See [Installation](../docs/INSTALL.md), [Architecture](../docs/ARCHITECTURE.md), [Operations](../docs/OPERATIONS.md), and the generated [permissions matrix](../PERMISSIONS.md).
