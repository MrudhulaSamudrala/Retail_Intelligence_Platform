# One-shot production collection for Windows Task Scheduler.
# Does not launch Streamlit. Uses the project .venv interpreter.
# Independent of the caller's current directory.
# Schedule times come from config/schedule.yaml (default 08:00 / 14:00 / 20:00 local).

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

& $Python -m collector.run --all *>> $LogFile
$Code = $LASTEXITCODE
Add-Content -LiteralPath $LogFile -Value ("exit_code=" + $Code)
exit $Code
