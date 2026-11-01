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

## Feedback

Authorized reviewers can confirm a fault, mark a false alarm, or request more data. Feedback records are attached to the anomaly review and do not rewrite original detector output.
