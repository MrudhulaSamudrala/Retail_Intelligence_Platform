# Register or update the BridgeAI production collection scheduled task.
# Times are read from config/schedule.yaml via the project Python.
#
# Usage (from repo root, PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows_scheduler.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "Project virtual environment Python not found: $Python"
    exit 1
}

$Launcher = Join-Path $RepoRoot "scripts\run_scheduled_collection.ps1"
$MetaJson = & $Python -c @"
from collector.orchestration.schedule import load_collection_schedule
import json
cfg = load_collection_schedule()
print(json.dumps({
    'hours': list(cfg.hours),
    'minute': cfg.minute,
    'task_name': cfg.task_name,
    'timezone': cfg.timezone_name,
}))
"@
$Meta = $MetaJson | ConvertFrom-Json
$TaskName = [string]$Meta.task_name
$Minute = [int]$Meta.minute

$Triggers = @()
foreach ($Hour in $Meta.hours) {
    $At = Get-Date -Hour ([int]$Hour) -Minute $Minute -Second 0
    $Triggers += New-ScheduledTaskTrigger -Daily -At $At
}

$Arg = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Launcher`""
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arg -WorkingDirectory $RepoRoot
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
    -Hidden
# Interactive + current user: uses the logged-on Windows session (needed for the
# existing Chrome/CDP profile). Do not embed passwords in this repo. To run when
# nobody is logged on, set credentials in Task Scheduler UI — not in git.
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Principal $Principal `
    -Description "BridgeAI production collection (python -m collector.run --all). Does not start Streamlit." `
    -Force | Out-Null

Write-Host "Scheduled task registered: $TaskName"
Write-Host ("Daily times (machine local timezone): " + (($Meta.hours | ForEach-Object { "{0:D2}:{1:D2}" -f $_, $Minute }) -join ", "))
Write-Host "Launcher: $Launcher"
Write-Host "Python:   $Python"
Write-Host "Working directory: $RepoRoot"
Write-Host "Change hours in config/schedule.yaml, then re-run this script."
