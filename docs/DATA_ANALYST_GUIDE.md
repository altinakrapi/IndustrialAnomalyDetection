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

The Anomalies page groups operational registered-equipment results by device, then by parameter. Expand a device to review its newest measurements; each device page contains up to 50 measurements. Use the flat-list toggle to change presentation. Device, period, source, and status filters and CSV export are available in either view. Device totals count anomaly rows; the separate measurement-window count deduplicates multiple anomaly rows for the same measurement. This is not an episode tree and it has no separate unlinked-episode group. The operational view excludes archived devices, on-demand/history/evaluation/tuning/comparison/validation runs, and NASA IMS dataset anomalies; those dataset anomalies remain in dataset analysis. Feedback and maintenance-order actions are available from an individual anomaly, not from an episode. An episode-feedback API operation exists, but it is not an episode-tree UI workflow.
