# Simulator

The standalone simulator under `tools/sensor_simulator` generates synthetic measurements and sends them to the normal ingestion API. It does not read PostgreSQL or call Python detectors directly.

## Setup

Copy and provision the example configuration:

```powershell
cd tools\sensor_simulator
Copy-Item config.example.json config.json
$env:SIMULATOR_ADMIN_EMAIL = "<administrator-email>"
$env:SIMULATOR_ADMIN_PASSWORD = Read-Host "Administrator password"
python .\simulator.py --config .\config.json setup
Remove-Item Env:SIMULATOR_ADMIN_EMAIL
Remove-Item Env:SIMULATOR_ADMIN_PASSWORD
```

Do not put API keys or passwords in `config.json`.

## Run

```powershell
python .\simulator.py --config .\config.json run
```

Override duration, speed, or seed only when needed:

```powershell
python .\simulator.py --config .\config.json run --duration 120 --speed-up 60 --seed 41
```

The simulator writes ground-truth JSONL ledgers under `runs/` and refuses to overwrite an existing path. Simulator measurements are synthetic and remain labelled `simulated`.

## Full integration runner

The repository integration entry point is:

```powershell
.\run_full_integration.ps1
```

It requires local `config.json` and `devices.local.json`, plus the two simulator administrator environment variables. Do not treat direct reserved-seed commands as the maintained integration runner.

## Tests

```powershell
python -m unittest discover -s tests -v
```
