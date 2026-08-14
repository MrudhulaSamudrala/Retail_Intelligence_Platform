# Windows Task Scheduler — BridgeAI production collection

BridgeAI collection is a **one-shot** process. Streamlit is only the dashboard.
Windows Task Scheduler runs the collector independently of Cursor.

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

`--all` always runs the complete production pipeline on **one** `collection_run_id`:

1. Newegg
2. Mercado Libre
3. Audits
4. Badges
5. Pricing
6. Banners
7. Search

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

The dashboard Collection Status “next collection” hint reads the same file.

## Register the task (run this once)

From the BridgeAI repository root, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows_scheduler.ps1
```

Until you run that command, the task exists **only as repository configuration**. Setup is not performed automatically.

This creates or updates the task named **`BridgeAI - Production Collection`**.

The task:

- uses `.venv\Scripts\python.exe` (not whatever `python` is on PATH)
- starts in the repository root
- runs hidden (no extra terminal, no browser UI for you to drive)
- does **not** start Streamlit
- writes logs to `logs/collections/`
- uses `MultipleInstances: IgnoreNew` so a 14:00 tick will **not** start a second collection if 08:00 is still running
- sets **Wake the computer to run this task** (Windows wake timers must also be allowed in power settings)
- runs as the **logged-on Windows user** (Interactive). That matches the existing Chrome/CDP profile. Passwords are **not** stored in the repository.

If you need the task to run when nobody is logged on, set credentials in the Task Scheduler UI (`Run whether user is logged on or not`). Do not put those credentials in git.

## Verify the task exists

```powershell
schtasks /Query /TN "BridgeAI - Production Collection" /V /FO LIST
```

## Manual one-off run (live collection)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_scheduled_collection.ps1
```

Equivalent:

```powershell
.\.venv\Scripts\python.exe -m collector.run --all
```

## Test the wrapper without a live collection

This uses the existing collector `--dry-run` path (config/DB/Playwright checks only; no observation inserts):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_scheduled_collection.ps1 -DryRun
```

## Logs

Each tick writes a **new** file (historical logs are kept):

`logs/collections/collection_YYYY-MM-dd_HH-mm-ss.log`

The file includes start/end timestamps, the exact Python command, collector stdout (run ID, overall status, component status, report paths/errors), and the process exit code.

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

## Sleep / laptop

If the machine is asleep at 08:00 / 14:00 / 20:00, Windows can wake it when **WakeToRun** is set **and** wake timers are enabled in Power Options. If the PC is fully powered off, the tick is missed; `-StartWhenAvailable` will run it after the next boot while you are logged on.

## Failure handling

A PARTIAL or FAILED component does not disable the scheduled task. The next 08:00 / 14:00 / 20:00 still fires. Collection Status on the dashboard continues to show the last run as SUCCESS / PARTIAL / FAILED with per-component badges.
