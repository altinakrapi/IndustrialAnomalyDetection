# Industrial Anomaly Detection

Industrial Anomaly Detection is a role-based web application for monitoring equipment, reviewing statistical anomalies, managing maintenance work, and reporting operational evidence.

## What it does

- Ingests manual, sensor-key, simulator, and imported dataset measurements.
- Applies atomic per-device sensor limits based on reading count rather than batch count.
- Runs Isolation Forest and Z-score analysis against stored history.
- Presents anomaly and severity evidence for human review.
- Supports administrator, data-analyst, and maintenance-technician workflows.

## What it does not do

- It does not assume a physical sensor is connected.
- It does not claim validated physical-fault precision, recall, or F1 for NASA IMS data.
- It does not automatically remove extreme measurements.
- It does not use tuned configurations as production detector defaults.

## Quick start on Windows

```powershell
# 1-6: backend setup and start
cd Server; py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Create PostgreSQL database; set DATABASE_URL and SECRET_KEY in .env.
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m scripts.seed_admin
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
# 7-8: frontend, in a second terminal
cd Client; npm.cmd install
npm.cmd run dev
# 9-10: simulator, in a third terminal
cd tools\sensor_simulator; python .\simulator.py --config .\config.json setup
python .\simulator.py --config .\config.json run
```

Sign in, open **Overview**, then open **Anomalies**. Simulator readings remain labelled `simulated`.

## Database migrations

- The recovered `9e2a7c4f1b6d_add_email_delivery` migration restores the email preferences and outbox schema.
- Migration and database stamp repairs are performed with the application's explicit backup and review procedure; never run an upgrade, downgrade, or stamp against an undisclosed database.

## Documentation

- [Installation](docs/INSTALL.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Detectors](docs/DETECTORS.md)
- [Alerts and severity](docs/ALERTS_AND_SEVERITY.md)
- [Sensor API](docs/SENSOR_API.md)
- [Simulator](docs/SIMULATOR.md)
- [Evaluation](docs/EVALUATION.md)
- [Operations](docs/OPERATIONS.md)
- [Glossary](docs/GLOSSARY.md)
- [Limitations](docs/LIMITATIONS.md)
- [Changelog](docs/CHANGELOG.md)
- [Administrator guide](docs/ADMINISTRATOR_GUIDE.md) / [Shqip](docs/sq/ADMINISTRATOR_GUIDE.md)
- [Data Analyst guide](docs/DATA_ANALYST_GUIDE.md) / [Shqip](docs/sq/DATA_ANALYST_GUIDE.md)
- [Maintenance Technician guide](docs/MAINTENANCE_TECHNICIAN_GUIDE.md) / [Shqip](docs/sq/MAINTENANCE_TECHNICIAN_GUIDE.md)
