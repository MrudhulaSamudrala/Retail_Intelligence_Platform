# Deployment — Production Collection Runner

## Overview

BridgeAI production collection is a **one-shot process**:

```bash
python -m collector.run --all --scheduled
```

It orchestrates existing collectors (products, audits, badges, pricing snapshots,
homepage banners, search visibility), writes a `collection_runs` row plus
`collection_run_steps`, then **exits**. There is **no** in-process scheduler loop.

Scheduling belongs to the host — **Render Cron Job** or **Windows Task Scheduler**.
Use `--scheduled` (or `COLLECTION_TRIGGER=scheduled`) so run metadata records
`source=scheduled`. Manual runs remain `python -m collector.run --all` (`source=cli`).

---

## Command reference

| Command | Purpose |
|--------|---------|
| `python -m collector.run --all` | Full production pipeline |
| `python -m collector.run --all --dry-run` | Validate DB/config/Playwright; no observation inserts |
| `python -m collector.run --retailer newegg` | Newegg products + dependent product observations |
| `python -m collector.run --retailer mercadolibre` | Mercado Libre product step (adapter may be pending) |
| `python -m collector.run --step audits` | Audits only |
| `python -m collector.run --step pricing` | Pricing step report only |
| `python -m collector.run --step banners` | Homepage banners only |
| `python -m collector.run --step search` | Search visibility only |
| `python -m collector.run --legacy-product-only --retailer newegg --limit 20` | Legacy product pipeline JSON summary |

Config defaults: `config/orchestration.yaml`.

---

## Timezone and 3× daily schedule

**Project timezone for scheduling documentation: `UTC`**
(see `config/retailers.yaml` → `scheduling.timezone`).

### Recommended wall-clock times

| Local intent (UTC) | Cron (Render uses UTC) |
|--------------------|-------------------------|
| 08:00 UTC | `0 8 * * *` |
| 14:00 UTC | `0 14 * * *` |
| 20:00 UTC | `0 20 * * *` |

**Combined Render Cron expression (UTC):**

```text
0 8,14,20 * * *
```

This invokes the job three times per day at **08:00, 14:00, and 20:00 UTC**.

### If you need a non-UTC local zone

Render Cron evaluates expressions in **UTC**. Convert local times explicitly.

Example — desired **America/New_York** (US Eastern) wall times 08:00 / 14:00 / 20:00:

| Eastern | UTC (EST, UTC−5) | UTC (EDT, UTC−4) |
|---------|------------------|------------------|
| 08:00 | 13:00 | 12:00 |
| 14:00 | 19:00 | 18:00 |
| 20:00 | 01:00 (+1 day) | 00:00 (+1 day) |

DST means a single static cron cannot track Eastern wall clocks year-round; prefer
documenting the schedule in UTC, or adjust the expression when clocks change.

Example — desired **America/Sao_Paulo** (UTC−3, no DST currently) at 08:00 / 14:00 / 20:00:

```text
0 11,17,23 * * *
```

(08:00−3→11:00 UTC, etc.)

### Relation to older assumptions

`docs/assumptions.md` A16 previously cited `0 2,10,18 * * *` UTC for evenly
spaced coverage. The **production runner** standard is **`0 8,14,20 * * *` UTC**
to match the brief’s 08:00 / 14:00 / 20:00 windows in the project timezone (UTC).
Update Render to the expression above when enabling the production job.

---

## Render Cron Job setup

1. Create a **Cron Job** service on Render (same repo / Docker image as the app).
2. **Command:**

   ```bash
   python -m collector.run --all --scheduled
   ```

3. **Schedule:** `0 8,14,20 * * *` (UTC).
4. Attach the same **environment variables** as the web/worker service:
   - `DATABASE_URL` (Render PostgreSQL)
   - Playwright / browser settings as required (`COLLECTION_CDP_URL` if using CDP)
   - Any retailer-specific non-secret config
5. Do **not** put database passwords or API keys in source or cron command text.
6. Ensure migrations are applied before the first cron tick:

   ```bash
   alembic upgrade head
   ```

### Concurrent-run protection

The runner acquires a PostgreSQL **advisory lock** (`config/orchestration.yaml` →
`concurrent_lock_key`) and refuses to start if another production run is
`RUNNING`. Overlapping cron ticks exit safely (exit code `0`) with a skip message.

Stale `RUNNING` rows older than `stale_running_hours` are marked `FAILED`.

---

## Exit codes (Render monitoring)

| Overall status | Exit code | Notes |
|----------------|-----------|--------|
| `SUCCESS` | `0` | All enabled components succeeded |
| `PARTIAL` | `0` (default) | Configurable via `orchestration.exit_code_partial` (set `2` to alert) |
| `FAILED` | `1` | All enabled components failed, or orchestration crash |
| Skipped (lock busy) | `0` | Safe no-op; not treated as failure |
| Dry-run fail | `1` | Validation unsuccessful |

---

## Data semantics (idempotency)

| Table | Write mode |
|-------|------------|
| `products` | **Upsert** by stable retailer identity (no duplicate masters per SKU/URL) |
| `product_snapshots` | **Append-only** |
| `price_history` | **Append-only** |
| `promotions` | **Append-only** |
| `retailer_audits` | **Append-only** |
| `badges` | **Append-only** |
| `banner_observations` | **Append-only** |
| `search_observations` | **Append-only** |
| `collection_runs` | Insert + finalize status |
| `collection_run_steps` | Insert + finalize status |

Historical observations are **never** truncated by the production runner.
Each scheduled run creates a new parent `collection_runs` row (`run_type=production`).

---

## Logging

Structured logs include `run_id`, `component`, `status`, `records_processed`, and
errors. Secrets must not appear in logs. Final stdout prints the production summary
block used for operator verification.

---

## Local validation checklist

```bash
# 1. Migrate
alembic upgrade head

# 2. Dry-run
python -m collector.run --all --dry-run

# 3. First full run
python -m collector.run --all

# 4. Confirm collection_runs / collection_run_steps / append-only growth

# 5. Second run — new run_id; historical rows preserved; products upserted
python -m collector.run --all
```

Do not configure production Render credentials from this documentation automatically.

---

## Windows Task Scheduler

See **`docs/windows_scheduler.md`**. Times are configured in **`config/schedule.yaml`**
(default 08:00 / 14:00 / 20:00 in the machine local timezone).

Register:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows_scheduler.ps1
```

Task name: **BridgeAI - Production Collection**. The launcher is
`scripts/run_scheduled_collection.ps1`, which runs
`.venv\Scripts\python.exe -m collector.run --all` from the repository root
and writes logs under `logs/collections/`. Streamlit is not started.

Overlapping ticks are skipped by the existing advisory lock / RUNNING-run check.
