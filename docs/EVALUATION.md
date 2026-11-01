# Evaluation

## Simulator evaluation

Simulator evaluations require stored ground truth and synthetic readings. Metrics are valid only for the simulator seeds, tolerance, scored positions, and metric version recorded in the result. The UI must retain this limitation.

## Tuning

GET and POST tuning endpoints are restricted to Administrator and Data Analyst. Maintenance Technicians receive 403 and do not need tuning UI requests.

Tuned configurations are development evidence, not supported detector defaults. The ensemble members and selected configurations are only evidence of the recorded run.

## NASA IMS validation

NASA IMS Bearings archives can be imported for statistical review. NASA source files do not provide reliable per-window fault labels. The product therefore does not claim window-level precision, recall, or F1 for NASA IMS.

The default window size is 256 readings. The importer enforces a 10 GiB expanded archive limit. Processing disk needs depend on the archive contents, extraction, database rows, generated windows, artifacts, and the selected window size. There is no fixed guess in the code. Large imports can take tens of minutes to multiple hours; leave enough free disk for extraction, database growth, and temporary files before starting.

Validation lifecycle history and tuning run history belong in [CHANGELOG.md](CHANGELOG.md).
