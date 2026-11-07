# Alerts and severity

Severity is statistical evidence for human review. It is not a physical-failure diagnosis.

## Source types

Each device or measurement has one of:

- `manual`
- `sensor`
- `simulated`
- `dataset`

The UI and reports retain this provenance and label generated or simulated data.

## Review information

Severity can use configured limits, detector agreement, persistence, and prior registered-device scores. Limit and flatline behavior is separate from the general statistical score logic.

Existing score history can raise reviewer-facing evidence only when the required prior history exists. An administrator can inspect the evidence used to calculate and recompute severity.

## Operational anomaly view

The registered-equipment view includes only non-archived registered devices and live `measurement` analyses whose anomaly measurement matches the run measurement and whose parameter belongs to the run device. It excludes on-demand/history/evaluation/tuning/comparison/validation runs and NASA IMS dataset anomalies. Device anomaly counts are anomaly rows; unique measurement-window counts are reported separately because multiple anomaly rows can refer to one measurement.

## Feedback

Authorized reviewers can confirm a fault, mark a false alarm, or request more data. Feedback records are attached to the anomaly review and do not rewrite original detector output.

The current Anomalies page is grouped by device and parameter, not by episode, and has no separate unlinked-episode group. Per-anomaly feedback and maintenance-order actions are available from anomaly review. An episode-feedback API operation exists, but there is no episode-tree UI or episode-based maintenance-order action.
