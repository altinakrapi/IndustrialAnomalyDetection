# Data Analyst guide

## Import and explore datasets

Open **Datasets** to upload or import data. Open **Exploration** to inspect dataset statistics.

## Run an analysis

Open **Analysis**, choose the model configuration and input data, and run the analysis. Open the saved result to review output without treating it as a diagnosis.

Analysis history separates registered equipment from NASA IMS datasets and collapses automatic per-reading equipment runs into a 24-hour summary; expand it to view the paginated readings. Device analyses use the selected parameter and its recorded source type. Comparison snapshots and simulator evaluations remain separate from analysis-run history. Administrators and Data Analysts can run analyses and compare results; Maintenance Technicians have read-only access.

## Evaluate models

Open **Evaluation** to review simulator evaluations separately from NASA IMS validation. Simulator Precision, Recall, F1, false-alarm rate, and detection delay are computed only from stored labelled runs; development-purpose results are for exploring settings, while evaluation-purpose results are the held-aside check. NASA IMS provides descriptive bearing and time evidence, but has no per-window fault labels for accuracy metrics. Treat all scores and alerts as statistical evidence for human review, not a physical-failure diagnosis. Administrators and Data Analysts can create simulator evaluations and tune configurations; Maintenance Technicians can review results read-only.

## Compare and tune

Use the available **Analysis** and **Evaluation** controls. Tuning creation and reads are available to Administrators and Data Analysts.

## Create maintenance from an anomaly

Open **Anomalies**, inspect the source type and evidence, then create a maintenance order. This must remain reviewer evidence rather than a physical-fault determination.

The Anomalies page groups operational registered-equipment results by device, then by parameter. Expand a device to review its newest measurements; each device page contains up to 50 measurements. Use the flat-list toggle to change presentation. Device, period, source, and status filters and CSV export are available in either view. Device totals count anomaly rows; the separate measurement-window count deduplicates multiple anomaly rows for the same measurement. This is not an episode tree and it has no separate unlinked-episode group. The operational view excludes archived devices, on-demand/history/evaluation/tuning/comparison/validation runs, and NASA IMS dataset anomalies; those dataset anomalies remain in dataset analysis. Feedback and maintenance-order actions are available from an individual anomaly, not from an episode. An episode-feedback API operation exists, but it is not an episode-tree UI workflow.
