# Detectors

## Isolation Forest

Isolation Forest estimates how isolated a feature vector is within the current stored training data and configuration. Its score is statistical evidence, not a physical-fault diagnosis.

## Z-score

Z-score analysis measures deviation from stored baseline statistics. Extreme values are retained; preprocessing never silently removes them.

## Ensemble behavior

An ensemble combines configured detector outputs. Its members are refitted against the growing stored history, so results can change as data grows. An older ensemble record is evidence about the history available at that run, not a fixed production model.

## Presentation

Detector names, scores, modes, baselines, and available explanation fields come from stored analysis records. Documentation and UI must not turn statistical output into physical failure claims.
