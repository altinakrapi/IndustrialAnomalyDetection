# Limitations

- NASA IMS Bearings has no reliable per-window labels, so the product does not report window-level precision, recall, or F1 for NASA IMS.
- Simulator evaluations use few selected seeds and the stored scored positions; they are development evidence, not physical validation.
- Existing tuning evidence is based on approximately 500 readings per selected simulator run only for those recorded runs; it is not a production data requirement.
- Tuned configurations are development evidence and are not detector defaults.
- Ensemble members are refitted on growing recorded history.
- Extreme readings are retained and reviewed rather than removed automatically.
- No physical sensor is assumed to be connected. Sensor ingestion requires external infrastructure and device API keys.
- Scores, alerts, and severity are statistical evidence for human review, not physical-failure diagnoses.
