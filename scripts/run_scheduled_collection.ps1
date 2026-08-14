# One-shot production collection for Windows Task Scheduler.
# Does not launch Streamlit. Uses the project .venv interpreter.
# Independent of the caller's current directory.
# Schedule times come from config/schedule.yaml (default 08:00 / 14:00 / 20:00 local).
#
# Live collection (Task Scheduler):
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_scheduled_collection.ps1
# Environment check only (no retailer collection):
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_scheduled_collection.ps1 -DryRun

param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "Project virtual environment Python not found: $Python"
    exit 1
}

$LogDir = Join-Path $RepoRoot "logs\collections"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile = Join-Path $LogDir "collection_$Stamp.log"

$env:COLLECTION_TRIGGER = "scheduled"
$env:PYTHONUNBUFFERED = "1"

$CollectorArgs = @("-m", "collector.run", "--all")
$Mode = "production"
if ($DryRun) {
    $CollectorArgs += "--dry-run"
    $Mode = "dry-run"
}

$Started = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
@(
    "started_at=$Started"
    "repo=$RepoRoot"
    "python=$Python"
    "command=$Python $($CollectorArgs -join ' ')"
    "log_file=$LogFile"
    "streamlit=not_started"
    "mode=$Mode"
) | Set-Content -LiteralPath $LogFile -Encoding utf8

& $Python @CollectorArgs *>> $LogFile
$Code = $LASTEXITCODE
$Ended = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -LiteralPath $LogFile -Value @(
    "ended_at=$Ended"
    "exit_code=$Code"
)
exit $Code
