# One-shot production collection tick for Windows Task Scheduler.
# Do not register this task automatically — create the OS job manually.
# Schedule (project timezone UTC): 08:00, 14:00, 20:00 daily.
# Command equivalent: python -m collector.run --all --scheduled

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:COLLECTION_TRIGGER = "scheduled"
python -m collector.run --all --scheduled
exit $LASTEXITCODE
