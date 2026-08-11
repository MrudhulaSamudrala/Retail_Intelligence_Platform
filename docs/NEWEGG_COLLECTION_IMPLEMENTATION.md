# Newegg Collection Implementation

**Project:** BridgeAI  
**Retailer:** Newegg US  
**Prototype scope:** Controlled real-data collection of up to 20 gaming laptop products  
**Status:** Implemented and validated against PostgreSQL 18 (`bridgeai` on `localhost:5433`)  
**Primary command:** `python -m collector.run --retailer newegg --limit 20`

This document describes the **actual** Newegg collector implementation that was completed and validated. It is based on the code in the repository, the problems encountered during live runs, the fixes applied, and the final successful collection into PostgreSQL.

It does **not** claim Mercado Libre, analytics, dashboard, or scheduling work. Those are out of scope for this phase.

---

## 1. OBJECTIVE

### What the Newegg collector was supposed to accomplish

Prove an end-to-end real-data pipeline:

**Newegg US website → Playwright browser automation → listing discovery → product-page enrichment → parsing/normalization → SQLAlchemy repositories → PostgreSQL 18 (`bridgeai`)**

For each collected product (where available), capture:

- Product name / title  
- Retailer SKU / product ID  
- Product URL  
- Retailer = Newegg (`newegg`)  
- Country = US  
- Product type  
- Brand (Intel / AMD / Qualcomm / Apple, else `UNKNOWN`)  
- OEM (Dell / HP / Lenovo / Acer / Asus / MSI / Apple, else `NULL`)  
- Processor, GPU, RAM, storage  
- Price and currency (`USD`)  
- Availability  
- Promotion / discount text when present  
- Collection timestamp  

Constraints enforced during implementation:

- Do **not** fabricate or seed product data.  
- Do **not** rebuild the database layer or change the schema unless absolutely necessary (none was required).  
- Do **not** use Docker for PostgreSQL.  
- Use the existing normalization and repository layers.  
- Keep historical observations append-only.  
- Isolate per-product failures so one bad page does not abort the whole run.

### Why a controlled 20-product prototype

A small, bounded prototype was chosen to:

1. Validate selectors against **live** Newegg markup before scaling.  
2. Reduce risk while debugging bot-protection and parsing issues.  
3. Keep runtime short enough for iterative diagnosis.  
4. Produce a clear, reviewable set of real records in PostgreSQL.

Discovery was limited to **one** clearly defined gaming-related search scope (Best Match “gaming laptop”), not the full retailer catalog.

---

## 2. ARCHITECTURE

### End-to-end flow

```text
Newegg US (live site)
    → System Chrome (optional CDP attach on port 9222)
    → Playwright (BrowserSession)
    → Listing discovery (search results page)
    → ListingCandidate[] (SKU, URL, title, listing price/promo)
    → Product page fetch (one page per candidate, up to --limit)
    → HTML / JSON-LD / specs table parsing
    → Normalization (brand, OEM, product type, price, availability)
    → CollectionPersister
    → SQLAlchemy repositories
    → PostgreSQL 18 database `bridgeai`
```

CLI orchestration:

1. `python -m collector.run --retailer newegg --limit 20`  
2. `collector/run.py` loads `.env`, builds the Newegg adapter, opens a DB session.  
3. `CollectionPipeline` starts a `collection_runs` row, runs discovery + product fetch, persists each success, completes the run.  
4. A JSON summary is printed to stdout (discovered / successful / failed / sample products / errors).

### Relevant modules and files

| Path | Role |
|------|------|
| `collector/run.py` | CLI entrypoint; builds retailer adapter; prints JSON run summary. |
| `collector/__main__.py` | Package entry that delegates to `collector.run.main`. |
| `collector/pipeline.py` | Shared discover → fetch → persist loop; per-product error isolation; run status. |
| `collector/browser.py` | Playwright session: launch **or** CDP connect; goto retries; screenshots. |
| `collector/base.py` | `RetailerCollector` ABC, `ListingCandidate`, `CollectionOutcome`. |
| `collector/normalize.py` | Brand / OEM / product type / price / availability normalization; `NormalizedProduct`. |
| `collector/persist.py` | Maps `NormalizedProduct` into repositories (run, product upsert, append observations). |
| `collector/logging_utils.py` | Structured JSON logging for collection events. |
| `collector/config_loader.py` | Loads YAML configs (brands, OEMs, retailers, product types). |
| `collector/retailers/newegg/collector.py` | Newegg adapter: discovery + product fetch + bot-challenge waits. |
| `collector/retailers/newegg/discovery.py` | Loads `config/newegg_discovery.yaml`. |
| `collector/retailers/newegg/listing.py` | Listing-page parsing, SKU extraction, dedupe helpers. |
| `collector/retailers/newegg/product_page.py` | Product-page parsing, specs, JSON-LD, bot markers. |
| `collector/retailers/newegg/selectors.py` | CSS/text selector lists and bot-challenge markers. |
| `config/newegg_discovery.yaml` | Prototype discovery URL and limits. |
| `database/connection.py` | PostgreSQL URL builder / engine / session helpers. |
| `database/models.py` | ORM models (unchanged for this prototype). |
| `database/repositories.py` | Append-only observation helpers + product identity upsert. |
| `tests/test_newegg_collector.py` | Offline unit tests for parsing / normalization. |
| `tests/test_postgres_integration.py` | Optional live Postgres connectivity / schema checks. |

### Scaffolding vs implemented

| Area | Status |
|------|--------|
| Newegg listing + product collection | **Implemented** |
| Normalization + persistence into Postgres | **Implemented** |
| `collector/parsers/` package | **Scaffold only** (`__init__.py`); real parsing lives under `newegg/` + `normalize.py` |
| `collector/audit/` | **Scaffold only** |
| Mercado Libre collector | **Not implemented** (`NotImplementedError` in runner) |
| Analytics / dashboard / scheduling | **Not implemented** |

---

## 3. PLAYWRIGHT IMPLEMENTATION

### How Playwright is used

BridgeAI uses the **async** Playwright API (`playwright.async_api`) inside `BrowserSession` (`collector/browser.py`).

The Newegg collector:

1. Opens a browser session (`async with BrowserSession()`).  
2. Creates pages with `session.new_page()`.  
3. Navigates with `session.goto(page, url)` (retries on navigation failure).  
4. Reads DOM content via Playwright locators.  
5. Optionally captures PNG screenshots under `data/screenshots/`.  
6. Closes product/listing pages after use.

### How the browser is launched or connected

`BrowserSession` supports two modes:

1. **Launch mode (default)**  
   - Starts Chromium (optionally `COLLECTION_BROWSER_CHANNEL=chrome`).  
   - Applies a small init script (e.g. `navigator.webdriver` undefined).  
   - Controlled by `COLLECTION_HEADLESS` (default `true`).

2. **CDP attach mode (used for the successful live Newegg run)**  
   - If `COLLECTION_CDP_URL` (or `PLAYWRIGHT_CDP_URL`) is set, Playwright calls `chromium.connect_over_cdp(url)`.  
   - Reuses an existing browser context from a **already running** system Chrome.  
   - On exit, Playwright disconnects; it does **not** kill the user’s Chrome process (`_owns_browser = False`).

### Why Chrome CDP was used

Direct Playwright-launched Chromium/Chrome was blocked by Newegg/Cloudflare with an “unusual traffic” interstitial (HTTP 403, page title often `Just a moment...`).

Attaching Playwright to a normal system Chrome started with remote debugging allowed listing and product pages to load successfully. That was the approach used for the validated 20-product run.

Documented as assumption **A24** in `docs/assumptions.md`.

### What `127.0.0.1:9222` means

- Chrome was started with `--remote-debugging-port=9222`.  
- That opens a local Chrome DevTools Protocol (CDP) HTTP endpoint at `http://127.0.0.1:9222`.  
- Playwright connects to that endpoint and controls the already-open Chrome session.  
- Traffic stays on localhost; it is not a remote proxy service.

Example Chrome start pattern used during validation:

```text
chrome.exe
  --remote-debugging-port=9222
  --user-data-dir=<local profile dir>
  --disable-blink-features=AutomationControlled
  --no-first-run
  https://www.newegg.com/p/pl?d=gaming+laptop
```

Env used for the successful run:

```text
COLLECTION_CDP_URL=http://127.0.0.1:9222
COLLECTION_TIMEOUT_MS=45000
COLLECTION_RETRIES=3
```

### How listing and product pages are accessed

- **Listing:** `NeweggCollector.discover_listings` opens the discovery URL from `config/newegg_discovery.yaml`, waits until listing selectors appear (or bot challenge clears), scrolls lightly, then extracts cards.  
- **Product:** `NeweggCollector.fetch_product` opens each candidate URL in a new page, waits briefly for challenge clearance if needed, then parses the product DOM.

Pipeline requests more listing candidates than the save limit (`limit * 2`) so that after dedupe there are enough candidates to fill `--limit`.

### Screenshots

`BrowserSession.screenshot(page, label=...)` writes timestamped PNGs to `SCREENSHOT_DIR` (default `data/screenshots/`).

Captured when useful:

- Listing discovery page  
- Bot-challenge / empty-listing failure  
- Each successfully parsed product page  
- Bot challenge on product pages  

Screenshot failures are logged as warnings and do not abort the run.

### Retries and timeouts

| Setting | Default | Purpose |
|---------|---------|---------|
| `COLLECTION_TIMEOUT_MS` | `45000` | Playwright default timeout / navigation timeout |
| `COLLECTION_RETRIES` | `3` | Navigation retries in `BrowserSession.goto` |
| Challenge wait loops | ~12 listing / ~8 product attempts | Short waits while Newegg challenge pages clear |

Between retries, backoff sleeps use `min(2 ** attempt, 8)` seconds.

Structured logs are emitted as JSON lines via `collector/logging_utils.py` (events such as `collection_started`, `listings_discovered`, `product_persisted`, `product_failed`, `collection_finished`).

---

## 4. NEWEGG LISTING COLLECTION

### Exact prototype scope

Controlled to **one** Best Match search for gaming notebooks:

```yaml
# config/newegg_discovery.yaml
discovery:
  - name: gaming_laptops
    product_type_hint: notebook
    url: "https://www.newegg.com/p/pl?d=gaming+laptop"
```

Rationale (assumption **A20**):

- Matches tracked gaming notebook intent.  
- Avoids `Order=1` (Lowest Price), which during live inspection surfaced accessories / refurbished junk instead of gaming laptops.  
- Keeps the first live run small and reproducible (`max_pages_per_query: 1`).

### Product card detection

`extract_listings_from_page` (`listing.py`) tries item containers in order:

1. `.item-cell`  
2. `.item-container`  
3. `div.product-item`  
4. `[class*='product-item']`

On the live Best Match page used during validation, `.item-cell` and `a.item-title` were present and usable (dozens of cards).

For each card it extracts:

- Title / href via listing title selectors  
- Current price via `.price-current` (and fallbacks)  
- Was-price via `.price-was`  
- Promo text via `.item-promo` / `.price-save` / `.price-note`  
- Optional feature bullets from `ul.item-features li` / `.item-features li`

If card selectors yield nothing, a fallback harvests `a[href*='/p/']` anchors (filtered to real product links).

### Product URL / SKU extraction

- Absolute URLs are built with `absolute_newegg_url`.  
- SKU extraction (`extract_item_number`) prefers:  
  1. `N########...` style item numbers in URL/title  
  2. Query params `Item` / `ItemNumber` / `item`  
  3. Path token after `/p/` **only if** it is long enough and not a known non-product stub  

Search URLs like `https://www.newegg.com/p/pl?d=gaming+laptop` are explicitly rejected (`is_product_href` / `INVALID_PATH_SKUS` including `PL`).

### Duplicate handling

- Listing stage: `dedupe_candidates` keeps the first occurrence of each `retailer_sku`.  
- Pipeline stage: in-run `seen_skus` skips duplicates; identity upsert in DB reuses the same product row for later observations.

### Important listing selectors

Defined in `collector/retailers/newegg/selectors.py`:

- Items: `.item-cell`, `.item-container`, `div.product-item`, `[class*='product-item']`  
- Titles: `a.item-title`, `.item-title`, `a[title]`, `a[href*='/p/']`  
- Price: `.price-current`, `li.price-current`, `[class*='price-current']`, `.goods-price`  
- Was price: `.price-was`, `li.price-was`, `[class*='price-was']`  
- Promo: `.item-promo`, `.price-save`, `.price-note`, `[class*='item-promo']`  
- Features: `ul.item-features li`, `.item-features li`

---

## 5. PRODUCT PAGE COLLECTION

### How product pages are opened

For each `ListingCandidate`, `fetch_product`:

1. Opens a new Playwright page.  
2. Navigates to `candidate.source_url` with retries.  
3. Waits through a short bot-challenge clearance loop if needed.  
4. Calls `parse_product_page(...)`.  
5. Screenshots the page.  
6. Closes the page.

One failed product raises into the pipeline’s per-item `try/except` and does not stop the rest of the run.

### Title, SKU, price, availability, promotions

| Field | Source (priority) |
|-------|-------------------|
| Title | `h1.product-title` → other `PRODUCT_TITLE_SELECTORS` → JSON-LD `name` → listing fallback title |
| SKU | Item number from product URL/title → listing fallback SKU |
| Price | `#landingpage-price .price-current` → `.product-price .price-current` → `.price-current` → JSON-LD offers → listing fallback |
| List / was price | `.price-was` → listing fallback |
| Availability | Product availability selectors / JSON-LD offers → normalized to `in_stock` / `out_of_stock` / `limited` / `unknown` |
| Promo | `.product-promo`, `.price-save`, `.item-promo` → listing promo fallback |

### Processor, GPU, RAM, storage

1. Spec tables via `SPEC_ROW_SELECTORS` (notably `#product-details table tr` and `table.table-horizontal tr` on live pages).  
2. Listing feature bullets merged when tables are sparse (`specs_from_feature_bullets`).  
3. `pick_spec` maps aliases (CPU / GPU / memory / storage).  
4. Title regex heuristics for processor/GPU when specs are missing.  

These values are stored on the `NormalizedProduct` DTO and also mirrored into `raw_payload` (assumption **A21** — no schema migration for dedicated columns).

### JSON-LD usage

`extract_json_ld` reads `script[type="application/ld+json"]` and looks for `@type` including `Product`. Used as a fallback for title, price, and availability when DOM extraction is incomplete.

### Fallback selectors

All critical fields use ordered selector lists in `selectors.py`. Parsing continues through fallbacks rather than requiring one exact DOM shape. Newegg A/B markup differences are the reason for multiple selectors.

Bot challenge detection uses text markers such as:

- `unusual traffic`  
- `verify you are human`  
- `just a moment`  
- `cf-browser-verification`  
- `attention required`  
- `checking your browser`

---

## 6. BRAND / OEM / PRODUCT TYPE

Implemented in `collector/normalize.py`, driven by YAML:

- `config/brands.yaml`  
- `config/oems.yaml`  
- `config/product_types.yaml`

### Brand detection

`detect_brand(*texts)` concatenates evidence (title, processor, GPU, category, specs) and matches aliases / processor families for:

- Intel  
- AMD  
- Qualcomm  
- Apple  

If no evidence matches → **`UNKNOWN`** (never invent a brand).

Short aliases use word-boundary matching via `_alias_matches` to avoid false substring hits.

### OEM detection

`detect_oem(...)` matches tracked OEMs:

- Dell, HP, Lenovo, Acer, Asus, MSI, Apple  

For standalone `cpu` / `gpu` product types, OEM is forced to **`NULL`**.  
If no OEM alias matches → **`NULL`** (not `UNKNOWN`).

Brand and OEM are independent attributes (example: ASUS laptop with AMD Ryzen → Brand=`AMD`, OEM=`Asus`).

### Product type detection

`detect_product_type` uses category/title/specs aliases from `product_types.yaml`.  
For this prototype’s discovery category `gaming_laptops`, collected notebooks typically normalize to `notebook`.  
If no match → `UNKNOWN`.

### UNKNOWN / NULL handling

| Concept | Missing-value rule |
|---------|--------------------|
| Brand | `UNKNOWN` |
| OEM | `NULL` |
| Product type | `UNKNOWN` |
| Availability | `unknown` when text missing/unrecognized |
| Spec fields | `NULL` / omitted in payload when not extracted |

---

## 7. DATABASE PERSISTENCE

### Collector → SQLAlchemy path

1. Pipeline creates/uses a SQLAlchemy `Session` from `database.connection`.  
2. `CollectionPersister.start_run` → `CollectionRunRepository.start` inserts `collection_runs`.  
3. For each successful `NormalizedProduct`, `save_product`:  
   - `ProductRepository.upsert_identity` (stable product row)  
   - `ObservationRepository.add_snapshot` (append-only)  
   - `add_price` when price+currency present (append-only)  
   - `add_promotion` when promo signals present (append-only)  
4. `session.commit()` after each successful product.  
5. On product failure: `session.rollback()` then continue.  
6. `complete_run` updates the run row with status / counts / error message.

### Tables that receive data

| Table | What is written |
|-------|-----------------|
| `collection_runs` | Run metadata, status, items_collected, timestamps |
| `products` | Stable identity (retailer/country/SKU), latest title/brand/OEM/type/URL |
| `product_snapshots` | Append-only historical observation including `raw_payload` specs |
| `price_history` | Append-only price observations |
| `promotions` | Append-only promo observations when applicable |

No Alembic schema change was required for this prototype. Specs are stored in `product_snapshots.raw_payload` JSON/JSONB.

### Timestamps

- Observation timestamps use UTC (`datetime.now(timezone.utc)`) at persist time.  
- Run `started_at` / `completed_at` are set by the collection-run repository helpers.

### Historical observations / duplicates

- Observation tables are **append-only** (repositories do not update prior observation rows).  
- Re-collecting the same SKU upserts identity attributes on `products` and **adds** new snapshot/price rows.  
- This preserves history across runs (e.g. run 1 artifacts remain; run 2 appended new snapshots).

---

## 8. REAL IMPLEMENTATION PROBLEMS

Only issues that actually occurred during this implementation are listed.

### 8.1 Newegg / Cloudflare “unusual traffic” blocking

**What happened:** Initial Playwright probes against the discovery URL returned HTTP **403**, page title **`Just a moment...`**, and a Newegg page stating unusual traffic was detected, with a Cloudflare human-verification widget.

**Symptom:** Selector counts for `.item-cell` / `a.item-title` / `.price-current` were **0**; screenshots showed the block page (Ray ID visible in the interstitial).

**Why:** Automated browser launches were treated as bot traffic by Newegg/Cloudflare.

**Diagnosis:** Live probes with Playwright Chromium (headless and headed), system Chrome channel, and persistent profile still received 403 / challenge pages. Separately, Cursor’s IDE browser could open the same URL successfully, confirming the site itself was reachable from the machine when not launched as a Playwright-controlled browser.

**Fix:** Use a normal system Chrome with `--remote-debugging-port=9222` and attach Playwright via `COLLECTION_CDP_URL=http://127.0.0.1:9222`.

**Result:** Successful. Listing and product pages loaded; collection completed.

### 8.2 Direct Playwright browser access problem

**What happened:** Launch-mode Playwright (`chromium.launch` / `channel='chrome'`) could not reliably reach real listing content.

**Approaches tried that did not unlock Newegg for automated launch:**

- Headless Chromium  
- Headed system Chrome  
- Persistent Chrome user-data dir under Playwright launch  
- Extra init-script stealth tweaks  
- Firefox install was downloaded, but the successful path ended up being CDP attach to Chrome rather than a Firefox-based collection run  

**Fix:** CDP attach mode in `collector/browser.py` (documented above).

**Result:** Successful for the validated run.

### 8.3 Chrome CDP solution details

**What happened:** After starting Chrome with remote debugging, Playwright `connect_over_cdp('http://127.0.0.1:9222')` saw an open Newegg listing tab (`.../p/pl?d=gaming+laptop&recaptcha=pass`), with healthy selector counts (e.g. ~42 `a.item-title`).

**Why it worked:** Playwright attached to an already-running interactive Chrome session instead of spawning a fresh automated browser context that Newegg rejected.

**Code change:** `BrowserSession` CDP branch + `.env` / `.env.example` documentation of `COLLECTION_CDP_URL`.

**Result:** Successful.

### 8.4 Async factory / await bug in listing extraction

**What happened:** First live collection run logged many warnings:

```text
RuntimeWarning: coroutine 'extract_listings_from_page.<locals>.factory' was never awaited
```

**Symptom:** Listing batch reported a high raw count, but unique discovered candidates collapsed to only **5**, and extraction quality was poor.

**Why:** Inside `extract_listings_from_page`, `factory` was declared `async def`, but `first_text` / `first_attr` called it **without** `await`, so locators were never obtained correctly.

**Diagnosis:** Terminal RuntimeWarnings + unexpectedly low unique discovery count.

**Fix:** Changed `factory` to a normal synchronous function returning `_card.locator(sel)`.

**Result:** Successful. Next run discovered **40** unique listings from **42** cards.

### 8.5 `/p/pl` false SKU / product issue

**What happened:** First run persisted a bogus product:

- SKU: `PL`  
- Title: `Sorry`  
- URL: `https://www.newegg.com/p/pl`  

**Why:** Newegg search URLs use `/p/pl?...`. Path-token SKU extraction treated `pl` as a product ID. Anchor fallback also harvested the search link.

**Diagnosis:** JSON run summary included the `PL` record; DB verification confirmed it.

**Fix:**

- `INVALID_PATH_SKUS` / minimum path-token length in `extract_item_number`  
- `is_product_href` rejects `/p/pl` search stubs  
- Unit tests assert search URLs do not become products  

**Result:** Successful for subsequent runs (no new `PL` products). The original `PL` row remains because observation/product history is append-only and was not deleted.

### 8.6 OEM false-positive matching (`hp` inside `HDMI`)

**What happened:** An MSI product was normalized with OEM=`HP` despite being MSI.

**Why:** `detect_oem` used substring matching. Alias `hp` matched inside words like `hdmi`.

**Diagnosis:** Inspected run summary OEM fields against product titles.

**Fix:** `_alias_matches` in `normalize.py` uses word-boundary regex for short aliases (length ≤ 3). Unit test added for MSI title containing `HDMI`.

**Result:** Successful (e.g. MSI Stealth normalized to OEM=`MSI` on the later run).

### 8.7 GPU matching issue (VRAM mistaken for GPU model)

**What happened:** Some products extracted GPU as values like `8GB` instead of `GeForce RTX 5060 ...`.

**Why:** Spec alias order / matching preferred generic `gpu` / `graphics` rows that held VRAM sizes rather than GPU model names (e.g. `GPU/VGA Type`).

**Fix:**

- Reordered GPU aliases to prefer `gpu/vga type`, `graphics card`, `gpu type`, `chipset` before generic keys.  
- `pick_spec` rejects VRAM-only patterns such as `\d+\s*GB` for GPU aliases.  

**Result:** Partially successful. Many products now get full GPU names; some pages still yield weaker values (e.g. `8G GDDR7`, `Dedicated`, `AMD`) depending on Newegg’s spec table wording. Coverage on final run: GPU present on **19/20**.

### 8.8 Lowest-price search scope produced junk listings

**What happened:** Early live inspection of `...&Order=1` (Lowest Price) showed accessories / cheap refurbished Chromebooks rather than gaming laptops.

**Why:** Sort order, not selector failure.

**Fix:** Prototype discovery URL switched to Best Match without `Order=1`:  
`https://www.newegg.com/p/pl?d=gaming+laptop`

**Result:** Successful. Featured gaming laptops (MSI, ASUS, etc.) appeared in results.

### 8.9 First incomplete collection run before fixes

**What happened:** Run **id=1** completed with only **5** products saved (including the bad `PL` row), because it ran before the async-factory and `/p/pl` fixes.

**Result:** Not the acceptance run. Used as diagnosis input. Final acceptance is run **id=2**.

---

## 9. FINAL SUCCESSFUL RUN

### Command

```bash
python -m collector.run --retailer newegg --limit 20
```

### Environment / configuration used

- PostgreSQL 18 native on Windows: `localhost:5433`, database `bridgeai`, user `postgres`  
- `.env` included `COLLECTION_CDP_URL=http://127.0.0.1:9222`  
- System Chrome already running with remote debugging on port **9222**  
- Discovery: `config/newegg_discovery.yaml` Best Match gaming laptop URL  
- No Docker  

### Outcome (collection run id = 2)

| Metric | Value |
|--------|--------|
| Status | `completed` |
| Collection run ID | **2** |
| Listings discovered (unique) | **40** |
| Successfully saved | **20** |
| Failed | **0** |
| Bot blocked flag | `false` |
| Skipped duplicates (in-run) | `0` |

### Database counts observed after the successful run

Approximate state verified via SQLAlchemy/`psycopg` queries:

| Metric | Observed |
|--------|----------|
| Newegg US products | **21** (20 good + 1 leftover `PL` from run 1) |
| Product snapshots (Newegg-related) | **25** |
| Price history rows (Newegg-related) | **24** |
| Run 1 | completed, `items_collected=5` |
| Run 2 | completed, `items_collected=20` |

### Field extraction coverage (run 2 snapshots)

| Field | Coverage |
|-------|----------|
| Price | 20/20 |
| Brand (not `UNKNOWN`) | 20/20 |
| OEM non-null | 18/20 |
| Processor | 20/20 |
| GPU | 19/20 |
| RAM | 19/20 |
| Storage | 19/20 |

OEM `NULL` cases were products whose manufacturer was outside the tracked OEM list (e.g. Thunderobot, ACEMAGIC).

### Example real records (from run 2)

Examples present in PostgreSQL / run summary:

1. **N82E16834156876** — MSI CROSSHAIR A16 … AMD Ryzen 9-8940HX / RTX 5060 — Brand `AMD`, OEM `MSI`  
2. **N82E16834156800** — MSI Stealth 16 AI … Intel Core Ultra7-255H / RTX 5060 — Brand `Intel`, OEM `MSI`  
3. **N82E16834236637** — ASUS TUF Gaming A16 … Ryzen 7 260 / RTX 5060 — Brand `AMD`, OEM `Asus`  
4. **N82E16834156740** — MSI Vector … Ultra 7 255HX / RTX 5070 Ti — Brand `Intel`, OEM `MSI`  
5. **2WC-0009-03847** — Dell Alienware 18 … Ultra 9 275HX / RTX 5090 — Brand `Intel`, OEM `Dell`  

Prices were read from live product pages (values such as `1209.00`, `1821.99`, `1899.00` USD appeared in the run summary). Listing cards sometimes showed different struck/list prices; the collector persists what the product-page price selectors / fallbacks returned at collection time.

### Test results after the successful run

Full suite: **69 passed**  
(`test_foundation.py` 53 + `test_database_layer.py` 6 + `test_newegg_collector.py` 6 + `test_postgres_integration.py` 4)

---

## 10. DATABASE VERIFICATION

Verification used the live engine from `database.connection` against PostgreSQL 18 on `localhost:5433` / db `bridgeai`.

Checks performed:

1. **Connectivity / version** — integration tests assert ping works and `SHOW server_version` starts with `18`.  
2. **Schema presence** — expected tables including `products`, `product_snapshots`, `price_history`, `collection_runs`, `alembic_version`, etc.  
3. **Run rows** — `collection_runs` contained run ids 1 and 2 with statuses and `items_collected`.  
4. **Product + snapshot join** — queried latest snapshots for Newegg SKUs (title, brand, OEM, price, `raw_payload` processor/GPU/RAM/storage, `observed_at`).  
5. **Coverage aggregate** — counted non-empty fields for `collection_run_id = 2`.

This confirmed records were not only printed in the CLI JSON summary but **actually stored** in PostgreSQL.

---

## 11. CLOUDFLARE / ACCESS LIMITATION

### What happened with direct Playwright Chromium

Automated launches were served a Newegg/Cloudflare interstitial (“unusual traffic” / “Just a moment...” / human verification). Listing selectors were absent; collection could not proceed in launch mode.

### What worked instead

A legitimate interactive Chrome browser was started locally with remote debugging enabled. Playwright attached to that browser over CDP (`http://127.0.0.1:9222`) and then performed normal page navigation and DOM reads on pages that Chrome had successfully loaded.

### Why this is a limitation

- Collection reliability depends on being able to open Newegg in a normal browser session first.  
- Pure headless CI-style Chromium launch is currently unreliable against Newegg’s bot protection from this environment.  
- Operators must understand the CDP prerequisite (`COLLECTION_CDP_URL`) for live Newegg runs.

### What this document does **not** describe

This project documentation does **not** prescribe CAPTCHA-solving services, credential stuffing, or other anti-bot bypass techniques. The working approach was ordinary browser access plus Playwright attachment for automation of parsing and persistence.

---

## 12. TESTING

### Offline / unit

`tests/test_newegg_collector.py` covers:

- SKU extraction (including rejection of `/p/pl`)  
- Listing card parse + dedupe  
- Brand / OEM / product type detection (including HDMI≠HP case)  
- Normalized product unknowns  
- Price parsing / bot marker detection / `pick_spec`  
- Qualcomm / Apple detection  

Also retained:

- `tests/test_foundation.py` — project/config/connection smoke tests  
- `tests/test_database_layer.py` — SQLite hermetic repository/history behavior  

### Live Postgres integration

`tests/test_postgres_integration.py` (skipped automatically if DB unreachable):

- Ping  
- Expected tables exist  
- Server version 18  
- Connection settings target localhost / 5433 / bridgeai / postgres  

### Final result

**69 tests passed** after the successful collection work.

---

## 13. CURRENT LIMITATIONS

### What works

- Controlled Newegg US gaming-laptop discovery  
- Playwright collection with CDP attach  
- Parsing + normalization for brand/OEM/type/price/availability/specs  
- Append-only persistence into PostgreSQL 18  
- Per-product failure isolation, retries, screenshots, structured logs  
- CLI runner `python -m collector.run --retailer newegg --limit N`  

### What is prototype-only

- Single discovery URL / one page of results  
- Soft limit of ~20 products for the validated run  
- CDP-oriented live access path for Newegg  
- Specs stored in JSON `raw_payload` rather than first-class columns  

### What still needs improvement

- GPU/RAM extraction quality on inconsistent Newegg spec tables  
- Occasional price differences between listing cards and product-page widgets  
- Cleanup policy for known-bad historical rows (e.g. leftover `PL`) if desired later  
- More resilient launch-mode access if CDP is unavailable  
- Broader discovery scopes (desktops, GPUs, multiple pages) once prototype is stable  

### What has not been implemented

- Mercado Libre collector  
- Audit engine (S1–P5) beyond scaffolding  
- Analytics, SoS/SoV, reports  
- Streamlit dashboard  
- Scheduling / Render deployment  
- Dedicated `collector/parsers` package logic (still scaffold)  

---

## 14. MANUAL IMPLEMENTATION GUIDE

How an experienced Python developer could rebuild the same Newegg prototype **manually** (without AI tools), following the same engineering path.

### 14.1 Environment setup

1. Create a venv; install `requirements.txt` (Playwright, SQLAlchemy, psycopg, Alembic, PyYAML, python-dotenv, pytest, etc.).  
2. Copy `.env.example` → `.env`; set Postgres to `localhost:5433` / `bridgeai` / `postgres`.  
3. Ensure Alembic migrations are applied (`alembic upgrade head`) so tables exist.  
4. Install Playwright browser binaries as needed (`playwright install`).  

### 14.2 Playwright setup

1. Start with a tiny script that opens one Newegg URL and prints `page.title()` + a screenshot.  
2. If launch mode is blocked, start system Chrome with `--remote-debugging-port=9222` and connect with `chromium.connect_over_cdp`.  
3. Encapsulate launch/CDP/goto/screenshot/retry in one browser helper (as in `collector/browser.py`).  

### 14.3 Manual DevTools inspection

1. Open `https://www.newegg.com/p/pl?d=gaming+laptop` in Chrome.  
2. Confirm sort is Featured/Best Match (not Lowest Price).  
3. Inspect a product card: note classes for container, title link, price, promo, feature bullets.  
4. Open one product detail page; inspect `h1`, price widget, specs table, and JSON-LD scripts.  
5. Record fallback selectors because Newegg markup varies.  

### 14.4 Selector discovery

Maintain ordered selector lists (item → title → price → specs). Validate counts with `document.querySelectorAll(...)` in DevTools before coding. Keep bot-challenge text markers for detection.

### 14.5 Listing parser

1. Iterate item cards.  
2. Extract href/title/price/promo.  
3. Derive stable SKU from item number / path.  
4. Reject search stubs like `/p/pl`.  
5. Dedupe by SKU.  
6. Cap candidates for the prototype.  

### 14.6 Product-page parser

1. Navigate each candidate URL.  
2. Extract title/price/availability/promo.  
3. Parse specs table + JSON-LD.  
4. Map aliases to processor/GPU/RAM/storage.  
5. Fall back to listing fields when product DOM is incomplete.  

### 14.7 Normalization

1. Load brand/OEM/product-type YAML.  
2. Keep brand ≠ OEM.  
3. Use `UNKNOWN` / `NULL` rules consistently.  
4. Use word-boundary matching for short aliases (`hp`, `msi`, etc.).  

### 14.8 PostgreSQL / SQLAlchemy persistence

1. Start a `collection_runs` row.  
2. Upsert product identity by `(retailer_code, country_code, retailer_sku)`.  
3. Append snapshot/price/promo rows; never rewrite old observations.  
4. Commit per product; rollback on failure.  
5. Complete the run with counts/status.  

### 14.9 Testing

1. Unit-test pure parsers with saved HTML snippets / synthetic strings (no network).  
2. Add regression tests for bugs you hit (`/p/pl`, HDMI≠HP).  
3. Keep optional live DB integration tests gated on connectivity.  

### 14.10 Debugging

1. Screenshot every failure class (challenge, empty listing, product parse miss).  
2. Log structured events with SKU/URL.  
3. Compare listing vs product extraction when fields look wrong.  
4. Query Postgres after each small run.  

### 14.11 Historical data handling

Treat each collection as a new observation set. Upsert identity; append history. Do not “fix” old rows in place when re-scraping.

### 14.12 Scale gradually

1. **1 product:** hardcode one known product URL; persist one snapshot.  
2. **5 products:** parse one listing page; take first 5 unique SKUs.  
3. **20 products:** enable `--limit 20` once selectors and CDP/access path are stable.  

Do not jump to full-catalog crawling before the 20-product prototype is trustworthy.

---

## 15. LESSONS LEARNED

1. **Validate live DOM before trusting selectors** — Newegg markup and sort orders materially change result quality.  
2. **Bot protection is part of the integration surface** — launch-mode Playwright failure was an environmental constraint, not a missing CSS selector.  
3. **CDP attach is a practical access mode** for local prototype collection when automated launches are blocked.  
4. **Small async mistakes are expensive** — one un-awaited factory coroutine collapsed discovery quality.  
5. **URL shape matters** — search routes like `/p/pl` must never be treated as product IDs.  
6. **Alias matching needs boundaries** — naive substring OEM/brand matching creates silent data corruption.  
7. **Spec tables are messy** — GPU/VRAM fields require prioritization and value sanitization.  
8. **Append-only history is valuable and constraining** — bad early rows remain unless explicitly cleaned by a later ops decision.  
9. **Prototype limits accelerate learning** — 20 products exposed most pipeline risks without full-catalog complexity.  
10. **Keep schema stable early** — storing specs in `raw_payload` unblocked delivery without migration churn.

---

## 16. NEXT STEPS

Based only on the current implementation, the logical next steps are:

1. **Stabilize access runbooks** — document operator steps for starting Chrome with CDP before Newegg runs (already partially in `.env.example` / assumptions).  
2. **Improve field quality** — tighten GPU/price extraction against more product templates; add fixtures from saved real HTML where legally/ practically appropriate.  
3. **Optional data hygiene** — decide whether to soft-flag or remove the known bad `PL` product from run 1 (without rewriting historical methodology).  
4. **Expand Newegg scope carefully** — additional gaming categories/pages only after selector regression tests exist.  
5. **Then** implement Mercado Libre as a second retailer adapter on the same pipeline (`CollectionPipeline` + repositories), without redesigning persistence.  
6. **Only after multi-retailer collection is solid** — audit engine, analytics, dashboard, and scheduling.

---

## Appendix A — Quick operator checklist (Newegg live run)

1. Ensure PostgreSQL 18 is up on `localhost:5433` and `.env` points at `bridgeai`.  
2. Start system Chrome with `--remote-debugging-port=9222`.  
3. Confirm `http://127.0.0.1:9222/json/version` responds.  
4. Set `COLLECTION_CDP_URL=http://127.0.0.1:9222`.  
5. Run: `python -m collector.run --retailer newegg --limit 20`  
6. Confirm JSON summary: `successful` ≈ limit, `failed` = 0 (ideally).  
7. Query `collection_runs`, `products`, `product_snapshots` to verify persistence.  
8. Run: `pytest -q`

---

## Appendix B — Key assumptions

- **A20** — Single Best Match gaming laptop discovery scope.  
- **A21** — Spec fields stored in `product_snapshots.raw_payload`.  
- **A22** — Native PostgreSQL 18 on Windows (`localhost:5433`), not Docker.  
- **A24** — Prefer Playwright CDP attach when Newegg blocks automated browser launch.

See `docs/assumptions.md` for full wording.
