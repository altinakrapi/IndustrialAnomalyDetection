# Sensor Simulator

This standalone Python CLI sends reproducible synthetic sensor measurements to
the Industrial Anomaly Detection API. It uses only HTTP endpoints: it does not
import Server code, access PostgreSQL, or call the analysis implementation
directly. The server stores and analyzes each accepted reading through its
normal sensor-ingestion pipeline.

Every provisioned simulator device is registered with `source_type=simulated`.
Measurements ingested for those devices also retain `source_type=simulated`.
They are synthetic test data, not readings from physical equipment or a live
sensor feed. Do not use this tool with a physical-device API key.

## Requirements and installation

Python 3.11 or later is required. The CLI and unit tests use only the standard
library; no product dependencies are required. From PowerShell:

```powershell
Set-Location C:\Users\Lenovo\OneDrive\Desktop\IndustrialAnomalyDetection\tools\sensor_simulator
Copy-Item config.example.json config.json
```

Keep `config.json`, `devices.local.json`, generated logs, and run records local.
The included `.gitignore` excludes these files, including the API keys.

## Configure and provision devices

Set the API URL in `config.json` or override it with
`SIMULATOR_API_BASE_URL`. Device setup requires credentials for an active
Administrator. Provide them through environment variables; do not put them in
the config file or source control:

```powershell
$env:SIMULATOR_ADMIN_EMAIL = "admin@example.com"
$env:SIMULATOR_ADMIN_PASSWORD = Read-Host "Administrator password"
python .\simulator.py --config .\config.json setup
Remove-Item Env:SIMULATOR_ADMIN_EMAIL
Remove-Item Env:SIMULATOR_ADMIN_PASSWORD
```

Setup uses the existing login, device, parameter, and admin-only API-key
endpoints. It checks the device code before creating anything, refuses to
adopt a same-code non-simulated device, adds only missing parameters, and
reuses the locally stored key when it is still active. If the key file is
missing or no longer matches an active key, setup provisions/rotates a key.
The new plaintext key is returned only once by the API and saved to the
git-ignored `devices.local.json` file with restrictive file permissions.
Protect that file and revoke/rotate keys if it is lost or exposed.

## Run

```powershell
python .\simulator.py --config .\config.json run
```

`duration_seconds` is wall-clock run time. `speed_up` scales simulated time
relative to wall time, while `send_interval_seconds` remains the wall-clock
spacing between samples. For example, a 30x speed-up and a 5-second send
interval advances simulated time by 150 seconds between sent samples.

Override those settings for a run without editing the config:

```powershell
python .\simulator.py --config .\config.json run --duration 120 --speed-up 60 --seed 41
```

The default run ID is derived from the seed and configuration; repeating the
same run may cause the API to deduplicate the same idempotency keys. Use a new
`--run-id` when a separate set of accepted measurements is needed:

```powershell
python .\simulator.py --config .\config.json run --run-id bench-test-02
```

The default output is `runs/ground_truth-<run-id>.jsonl`. Each output path must
be new; this prevents accidental overwriting of prior ground truth. Ctrl+C
stops the run and closes/fsyncs the ledger.

## Configuration

The example config covers a motor, pump, compressor, and fan. Each device has a
unique `device_code`, one of those `device_type` values, an operating profile
(`working_hours` or `continuous`), parameters, and per-parameter baseline and
noise values. Supported exact parameter names and units are:

| Parameter | Unit |
| --- | --- |
| Temperature | °C |
| Vibration RMS | mm/s |
| Pressure | bar |
| Current | A |
| Rotational Speed | RPM |

`parameter_limits` and `parameter_intervals_minutes` may be set per device to
configure the matching server parameter. Omitted limit values use the
simulator defaults; interval defaults to one minute. Units are not configurable
because the API requires exact unit matches. The server enforces its own
measurement validation. `parameter_intervals_seconds` can optionally set a
shorter expected interval for integration tests; the configured parameter
interval is restored by the full integration test afterward.

The full integration configuration uses a 0.75-second tick and a 3-second
expected interval on its dedicated simulated devices. With the production
default multiplier of three, the stale threshold is 9 seconds. Its 20-tick
dropout lasts about 15 seconds, long enough for the existing 5-second
scheduler poll to open an episode before readings resume; no production
threshold or ingestion limit is changed. Stale detection uses server receipt
time. The integration run labels these sources as simulated and does not
represent live equipment.

Signal generation combines the per-device baseline, a daily load cycle,
Gaussian noise, and optional `wear_per_simulated_hour`. Load contributes a
shared trend to load-sensitive parameters (including vibration, temperature,
and current). Each device is generated from its own baseline and profile.

Scheduled `faults` contain `device_code`, configured `parameter`, `type`,
`start_seconds`, `end_seconds`, positive `magnitude`, and optional `severity`.
Times are relative simulated seconds from the run start. Supported fault types:

* `point_spike`: brief additive spike at the configured sample time.
* `gradual_degradation`: increase ramps over the interval.
* `sudden_level_shift`: additive level change during the interval.
* `stuck_sensor`: holds the first generated value constant during the interval.
* `signal_dropout`: omits readings; it does not send zeroes.
* `increased_variance`: increases Gaussian noise during the interval.
* `slow_drift`: gradual additive drift during the interval.

`sample_count` optionally stops generating samples for an individual device
after the requested number of simulator ticks. When every configured device
has a `sample_count`, the simulator waits for all counts to complete even if
wall-clock `duration_seconds` has elapsed; this avoids truncating count-based
tests because of API request latency. The simulated-time horizon is extended
to cover those ticks. Devices without a sample count keep the existing
duration-based behavior. `random_faults_per_device` adds seeded random faults
to configured device parameters. `random_fault_duration_seconds` sets their
simulated duration. `seed` controls the signal and random fault schedule; same
seed and config produce the same simulated values and planned fault labels.
Server receive timestamps naturally depend on the actual run.

## Delivery and ground truth

Readings are sent in batches through `POST /api/v1/readings/batch`, with one
API key per device. The client rate-limits each device to the API's 120
readings/minute maximum, uses bounded retries/backoff for 429 and temporary
server/network failures, and does not retry permanent 4xx validation errors.
Errors are logged. Every generated value is flushed to the ground-truth file
before delivery, so rejected or failed API calls do not erase the generated
record. Accepted events also retain the server's `alert_evaluation` response
for integration verification. The ground-truth file is never read by the
product or its detection pipeline.

JSONL events share `run_id` and `seed`. Events include:

* `fault_scheduled`: device, parameter, fault ID/type, simulated start/end,
  severity, and magnitude.
* `reading_generated`: synthetic value, unit, simulated time, fault IDs, and
  idempotency key.
* `reading_omitted`: dropout interval and active fault IDs.
* `reading_accepted`: API measurement ID, server measured/received timestamps,
  HTTP status, duplicate status, and analysis status/count, where returned by
  the API.
* `delivery_retry`: device, attempt number, HTTP status or network error, and
  retry delay.
* `delivery_failed`: failed reading reference, error type, HTTP status when
  applicable, and API/network error.
* `run_started`, `simulation_finished`, and `run_finished`: run lifecycle.

The API's `received_at` is the authoritative server receipt time. If no
`sensor_timestamp` is supplied, the stored `measured_at` is set to that server
receive time. Analysis history is ordered by `measured_at` ascending and then
measurement ID ascending; its walk-forward training window contains earlier
measurements only. The simulator's `simulated_time_seconds` is a separate
virtual timeline and must not be interpreted as a physical sensor timestamp.

## Full detector integration test

The opt-in test provisions no devices and stores no credentials. It requires
the user's existing `config.json` and `devices.local.json`, plus active
Administrator/Data Analyst credentials in environment variables. The first
three configured devices must be dedicated simulated devices with an enabled
`Vibration RMS` parameter and local API keys. analysis selects this run's measurements using the earliest and latest server
receive timestamps returned for the accepted readings. This excludes earlier
partial runs without deleting them.
The test
submits readings through the public ingestion API, then invokes the
permission-checked equipment-history analysis API and comparison API.

`config.integration.json` fixes the seed and schedules all seven fault types
after 80 warm-up ticks on the main device. It uses 45 normal ticks between
fault intervals (more than the 30-reading EWMA/CUSUM baseline), 492 main
device ticks, and 160 ticks on each of two independent simulated devices.
The current integration config uses 492 main-device ticks, one parameter, and
20 scheduled dropout ticks, so it expects 472 accepted main-device readings.
It sends at 0.75-second real-time intervals (about 80 readings/minute, below
the unchanged 120-reading/minute device limit). Sample-count mode runs to the
requested counts even after the 300-second wall-duration setting; expect at
least six minutes including API latency. Dropout slots are omitted, not
delivered as zeroes.

The three dedicated integration parameters are temporarily configured with a
3-second expected interval. The stale-data rule uses K=3, so the server
threshold is 9 seconds. Twenty omitted ticks create a gap of about 15.75
seconds between the last pre-dropout and first post-dropout readings, long
enough for the existing 5-second scheduler poll to observe the stale interval
without changing production defaults. The test restores each parameter's
original expected interval as unittest cleanup runs. A stuck-sensor interval is constant by construction; whether a detector
flags that constant level depends on its statistical model. A dropout has no
measurement to score and is reported as an omission, not a detector miss.

The test obtains each minimum-history value from `GET /api/v1/analyses/detectors`
at runtime and fails if 80 warm-up readings are less than the largest
registered minimum plus 20%. It uses a 100-reading training window, refits
walk-forward detectors every 25 readings, a 30-reading control-chart baseline,
and an ensemble quorum of two. It checks control-chart detections, the isolated
spike's `watch` priority, ensemble agreement/confidence, source separation,
re-analysis reproducibility on the same persisted readings with the same seed,
and that each scored row's training end precedes the reading. The repeat
analysis is not a second ingestion run. Each attempt uses a new run ID and
receive-time window; it does not delete prior simulator or user data. The
ground-truth JSONL and simulator log are retained under `runs/` after failure
or success. The test prints generated/accepted/omitted counts and retry/final
failure counts by HTTP status and device before asserting that only the
scheduled dropout explains missing measurements. Autoencoder results are
printed but are not a pass/fail condition.
No Precision, Recall, F1, or classification accuracy is computed.

The integration runner uses the seed in `config.integration.json` by default.
Set `SIMULATOR_SEED` to an integer before invoking
`run_full_integration.ps1` to exercise another deterministic seed; the chosen
seed is written into that run's config and ground-truth ledger.

The flatline rule is evaluated on equipment readings independently of the
model detector: after at least 30 prior readings establish a variable baseline,
10 consecutive values are considered a flatline when their standard deviation
is at most 5% of the baseline standard deviation. Parameter settings may
override the consecutive-reading count and tolerance. A near-zero baseline
standard deviation disables this rule for that parameter. The resulting alert
wording is evidence for review ("sensor reading is constant, possible stuck
sensor"), not a physical-failure diagnosis.

For registered devices whose source is `sensor` or `simulated`, the scheduler
checks server `received_at` values every five seconds and opens a deduplicated
stale-data episode after three expected intervals without a reading. The same
sweep runs while scheduled dataset analysis is paused. It closes the episode
when reporting resumes. Manually entered measurements continue to use the
existing overdue-recording reminder instead.

The equipment-history results endpoint returns `history_state`,
`history_size`, and `history_required` as top-level fields on each result.
`result_details` contains detector-specific metadata (including
`training_end_position` and `source_type`) and does not duplicate those
history fields. `history_state` is `ready` after the detector's minimum
history is available, or `insufficient_history` before it. Ensemble
member-specific history states are included in the ensemble's
`result_details.detectors` entries.

Run the complete live test from PowerShell after setting credentials and
provisioning the three dedicated devices using the regular simulator setup:

```powershell
Set-Location C:\Users\Lenovo\OneDrive\Desktop\IndustrialAnomalyDetection\tools\sensor_simulator
$env:SIMULATOR_ADMIN_EMAIL = "admin@example.com"
$env:SIMULATOR_ADMIN_PASSWORD = Read-Host "Administrator password"
.\run_full_integration.ps1
Remove-Item Env:SIMULATOR_ADMIN_EMAIL
Remove-Item Env:SIMULATOR_ADMIN_PASSWORD
```

The full test is skipped by default. To run only its skip-safe test discovery:

```powershell
python -m unittest discover -s tests -p test_full_integration.py -v
```

## Tests

Run the standalone suite from this directory:

```powershell
python -m unittest discover -s tests -v
```

The integration test is skipped unless all of the following point to a
dedicated, already provisioned simulated device and a valid enabled parameter.
It sends one unique reading to the API and is intentionally opt-in:

```powershell
$env:SIMULATOR_INTEGRATION_API_URL = "http://127.0.0.1:8000"
$env:SIMULATOR_INTEGRATION_DEVICE_CODE = "SIM-MOTOR-01"
$env:SIMULATOR_INTEGRATION_DEVICE_KEY = Read-Host "Simulated device API key"
$env:SIMULATOR_INTEGRATION_PARAMETER = "Vibration RMS"
$env:SIMULATOR_INTEGRATION_UNIT = "mm/s"
$env:SIMULATOR_INTEGRATION_VALUE = "2.4"
python -m unittest discover -s tests -p test_integration.py -v
```
