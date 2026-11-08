# Data Analyst guide

## Import and explore datasets

Open **Datasets** to upload or import data. Open **Exploration** to inspect dataset statistics.

## Run an analysis

Open **Analysis**, choose the model configuration and input data, and run the analysis. Open the saved result to review output without treating it as a diagnosis.

## Evaluate models

Open **Evaluation**. Create simulator evaluations and view tuning evidence where it is supported. NASA IMS validation has statistical evidence but no per-window labels.

## Compare and tune

Use the available **Analysis** and **Evaluation** controls. Tuning creation and reads are available to Administrators and Data Analysts.

## Create maintenance from an anomaly

Open **Anomalies**, inspect the source type and evidence, then create a maintenance order. This must remain reviewer evidence rather than a physical-fault determination.

The Anomalies page groups operational registered-equipment results by device, then parameter, then calendar-time buckets; 10 minutes is the default, with 1-, 5-, 10-, 15-, 30-, and 60-minute options, and each row shows its time range in your browser's display timezone. Each bucket summarizes anomaly count, value range and unit, highest severity, detectors, review status, and unreviewed count; open a bucket to load its individual anomalies and use **Open details** for the existing explanation and feedback controls. Bucket pages contain up to 50 buckets, and bucket anomalies are loaded in pages of up to 50. The flat-list toggle, device, parameter, period, source, status, severity, detector and search filters, and CSV export remain available. Device totals count anomaly rows; the separate measurement-window count deduplicates multiple anomaly rows for the same measurement. The operational view excludes archived devices, on-demand/history/evaluation/tuning/comparison/validation runs, and NASA IMS dataset anomalies; those dataset anomalies remain in dataset analysis.

On **Monitoring**, review source-labelled device health, the unique-measurement anomaly total, recent device/parameter groups, and failed or scheduled analysis history. Schedule settings are available only to Administrators in **Settings**; Monitoring has no manual cycle-start control. Live equipment readings are analysed as they arrive, and stopped-reporting checks run regardless. NASA IMS records are historical experiments, and simulated readings are synthetic test data, not physical telemetry.
