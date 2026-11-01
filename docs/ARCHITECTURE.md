# Architecture

The product is a layered modular monolith with a React frontend, a FastAPI backend, PostgreSQL persistence, and Alembic migrations.

## Backend modules

Feature modules normally contain:

- `api`: FastAPI routes and request parsing.
- `application`: use cases and orchestration.
- `domain`: pure feature rules, constants, and exceptions.
- `infrastructure`: persistence adapters.
- `schemas`: API request and response models.

Shared SQLAlchemy models live in `app/shared/models`. Authentication, configuration, database sessions, security helpers, and common API dependencies are shared across modules.

## Background processing

Ingestion persists readings and triggers configured analysis work. Scheduled jobs and workers maintain monitoring behavior independently from the detector implementations.

## Frontend

The React client is under `Client/src`. Role-aware navigation and dashboards use backend permissions as the authoritative boundary. Shared UI components include loading, empty, error, confirmation, and source-provenance presentation.

## Provenance

Devices and measurements retain `manual`, `sensor`, `simulated`, or `dataset` source types. Source provenance remains visible to every role.
