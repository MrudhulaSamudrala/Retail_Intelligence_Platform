# Windows Task Scheduler — BridgeAI production collection

BridgeAI collection is a **one-shot** process. Streamlit is only the dashboard.
Windows Task Scheduler runs the collector independently.

## Architecture

```
Windows Task Scheduler
        ↓
scripts/run_scheduled_collection.ps1
        ↓
.venv\Scripts\python.exe -m collector.run --all
        ↓
PostgreSQL
        ↓
Report generator (Excel + PSV under reports/)
        ↓
Streamlit dashboard (Reports / Collection Status)
```

## Schedule (one config file)

Edit **`config/schedule.yaml`**:

```yaml
schedule:
  hours: [8, 14, 20]
  minute: 0
  timezone: local
  task_name: "BridgeAI - Production Collection"
  log_dir: logs/collections
```

Default wall-clock times on the **machine local timezone**:

- 08:00
- 14:00
- 20:00

Change hours only in that file, then re-run the setup script. Do not scatter times in Streamlit or collector modules.

## Register the task

From the BridgeAI repository root, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows_scheduler.ps1
```

This creates or updates the task named **`BridgeAI - Production Collection`**.

The task:

- uses `.venv\Scripts\python.exe` (not whatever `python` is on PATH)
- starts in the repository root
- runs hidden (no extra terminal)
- does **not** start Streamlit
- writes logs to `logs/collections/`
- runs whether or not you have PowerShell open (as long as you are logged on)

## Manual one-off run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_scheduled_collection.ps1
```

Equivalent:

```powershell
.\.venv\Scripts\python.exe -m collector.run --all
```

## Logs

Each tick appends stdout/stderr to:

`logs/collections/collection_YYYY-MM-dd_HH-mm-ss.log`

The process exit code is preserved for Task Scheduler history.

## Reports

After a **SUCCESS** or **PARTIAL** collection, report generation is attempted automatically.
A report failure does **not** mark the collection as failed.

Files:

```
reports/YYYY-MM-DD/BridgeAI_Report_Run_<id>_YYYY-MM-DD.xlsx
reports/YYYY-MM-DD/BridgeAI_Report_Run_<id>_YYYY-MM-DD.psv
```

Regenerate without collecting:

```powershell
.\.venv\Scripts\python.exe -m reporting --latest
```
