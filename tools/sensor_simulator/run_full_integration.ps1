$ErrorActionPreference = "Stop"

$simulatorDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $simulatorDirectory

foreach ($requiredFile in @("config.json", "devices.local.json")) {
    if (-not (Test-Path (Join-Path $simulatorDirectory $requiredFile))) {
        throw "Required local setup file is missing: $requiredFile. Configure and provision devices first."
    }
}
foreach ($requiredVariable in @("SIMULATOR_ADMIN_EMAIL", "SIMULATOR_ADMIN_PASSWORD")) {
    if (-not [Environment]::GetEnvironmentVariable($requiredVariable)) {
        throw "Set $requiredVariable in this PowerShell session before running the integration test."
    }
}

$env:SIMULATOR_FULL_INTEGRATION = "1"
try {
    python -m unittest discover -s tests -p test_full_integration.py -v
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Remove-Item Env:SIMULATOR_FULL_INTEGRATION -ErrorAction SilentlyContinue
}
