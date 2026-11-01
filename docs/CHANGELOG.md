# Changelog

## Evaluation and tuning history

- NASA validation 5 and validation 6 remain retained lifecycle entries without automatic-start actions or result tables.
- Simulator tuning runs 1 and 2 are earlier-metric evidence with 476 scored positions.
- Simulator tuning runs 4, 5, and 6 are retained historical evidence with different scored positions.
- Run 6 has 1,384 stored scored positions in the retained historical evidence.
- Run 7 is current development evidence with 824 scored positions, simulator ground-truth runs 11 and 12, seeds 101 and 202, and approximately 500 readings per run.
- Runs superseded by run 7 are not deleted. Superseded configurations remain visible as historical evidence rather than active defaults.
- NASA dataset validations do not have per-window labels and must not be presented as precision, recall, or F1 evidence.

## Documentation

- Documentation was reorganized by installation, architecture, operation, detector, sensor, simulator, evaluation, and role-specific task.
- The role matrix and current tuning permissions are documented from the generated `PERMISSIONS.md`.
