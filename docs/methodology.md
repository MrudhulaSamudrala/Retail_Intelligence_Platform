# BridgeAI Methodology

This document describes the planned methodology for data collection and metrics. Items marked **assumed** are implementation assumptions documented in `docs/assumptions.md` and made configurable where practical. Open brief gaps are tracked in `docs/clarifications.md`.

## Product discovery

**Goal:** Build a reproducible universe of in-scope listings per retailer/country.

**Approach:**

1. Start from retailer category and search entry points relevant to computing / gaming.
2. Include product types: Notebook, Desktop, Workstation, Tablet, CPU, GPU (gaming segment).
3. Exclude monitors, keyboards, cameras, gift cards, and other accessories (see `config/product_types.yaml`).
4. Capture retailer SKU, canonical URL, title, category breadcrumbs, and raw evidence.
5. Deduplicate by `(retailer_code, country_code, retailer_sku)`.

Discovery runs create/update `products` identity rows and append `product_snapshots`.

## Normalization

Raw retailer fields are mapped into a common schema:

| Field | Rule |
|---|---|
| Brand | Deterministic hierarchy (below); else `UNKNOWN` |
| OEM | Deterministic hierarchy (below); else `NULL` for components |
| Product type | Category → title → specs; else `UNKNOWN` |
| Price | Retailer-native amount + currency (no FX conversion by default) |
| Availability | Normalized to `in_stock` / `out_of_stock` / `limited` / `unknown` |

Raw category text and selected raw payloads are preserved for auditability.

## Brand attribution

Brand ∈ {Intel, AMD, Qualcomm, Apple, UNKNOWN}.

**Hierarchy (assumed, configurable via `config/brands.yaml`):**

1. Explicit processor / SoC manufacturer
2. Processor family match
3. Product title
4. Specification table
5. Description

If evidence remains ambiguous → **UNKNOWN** (never invent a brand).

Brand and OEM are independent. Example: ASUS laptop with AMD Ryzen → Brand=AMD, OEM=Asus. Apple systems → Brand=Apple, OEM=Apple.

## OEM attribution

OEM ∈ {Dell, HP, Lenovo, Acer, Asus, MSI, Apple} or `NULL`.

**Hierarchy (assumed):**

1. Explicit manufacturer / brand field
2. Product title
3. URL / domain metadata when appropriate
4. Specification information

Standalone CPU/GPU components typically have **OEM = NULL**.

## Pricing

- Observe price at each pricing collection run (planned **3× per day**).
- Store on each append-only **product snapshot**: current price (`price_amount`), original price (`list_price`), discount percentage (`discount_pct`), promotion text (`promo_text`), and timestamp (`observed_at`).
- Mirror priced observations in append-only `price_history` (with currency / discount amount / promo flag) and deal text in `promotions`.
- Preserve retailer-native currency (**USD** for Newegg, **BRL** for Mercado Libre).
- Do **not** convert currencies unless a specific cross-country comparison explicitly requires it.
- Append only — never overwrite prior prices, snapshots, or promotions.

### Pricing / promotion analytics

Implemented in `analytics/pricing/` over real DB rows:

| Metric | Function |
|---|---|
| Average / median price by brand | `average_price_by_brand`, `median_price_by_brand` |
| Average discount | `average_discount` |
| Discounted product count | `count_discounted_products` |
| Price change over time | `price_change_over_time` |
| Discount change over time | `discount_change_over_time` |
| Retailer / country / product-type comparison | `compare_by_retailer`, `compare_by_country`, `compare_by_product_type` |

Cross-sectional metrics use the **latest** `price_history` row per product within an optional `PricingScope`. Time series bucket all observations by UTC day. Currencies are never auto-converted.

## Promotions

- Extract promo text, codes, discount value/unit, and visible deal windows when present.
- Link promotions to the product and collection run.
- Append to `promotions` on every observation where a promo signal is present (or explicitly absent, if collectors record negative evidence later).

## Retailer audits (S1, S2, P1–P5)

Checks follow the project brief and are implemented in `collector/audit/`:

| Code | Brief definition |
|---|---|
| S1 | Listing page title includes brand name and/or brand-specific processor line |
| S2 | Brand badge is present on the listing tile |
| P1 | Product page title includes brand name, processor line or generation |
| P2 | Brand badge is present on the product page |
| P3 | Brand or processor line appears in the specification table |
| P4 | Brand-led rich media is present |
| P5 | OEM rich media is present |

**Results:** `PASS` | `FAIL` | `UNKNOWN`

- `PASS` = evidence supports compliance for that check
- `FAIL` = evidence is present and does not support compliance
- `UNKNOWN` = insufficient evidence / collection or inspection failure

**UNKNOWN handling (assumed):** excluded from the compliance score denominator; coverage reported separately. UNKNOWN never auto-converts to PASS or FAIL. Missing information must not be treated as PASS.

Overall Brand Compliance Score is **not** computed in the audit-engine phase; individual checks are validated first.

## Overall Brand Compliance Score

Implemented in `analytics/compliance/`. Per brand, per retailer, and/or per country:

1. For each audit check (S1–P5), compute pass rate over non-UNKNOWN results.
2. Combine the seven checks using a **configurable** strategy (`config/compliance.yaml` → `check_aggregation.strategy`). Individual S1–P5 weights are **not** specified by the brief — see `docs/clarifications.md` (C1). Interim default: `equal_check_weights`.
3. Compute **Notebook** segment score and **Desktop** segment score separately.
4. Final weighted score (brief-authoritative):

```
Overall = (Notebook_score × 0.85) + (Desktop_score × 0.15)
```

If either Notebook or Desktop lacks scored data, `overall_score` is null (segment scores still reported; see clarifications C2).

Do **not** apply Notebook/Desktop weighting to Workstations, Tablets, CPU, or GPU unless explicitly required by the project owner. Those types may appear under `other_segments` for reporting only.

## Badge detection and relevance

- Detect **platform / processor badge families** and promotional badges using `config/badges.yaml`.
- Platform families: Intel (Core, Core Ultra, Evo, vPro), AMD (Ryzen, Ryzen AI), Qualcomm (Snapdragon), Apple (Apple Silicon, M-series).
- For each product, compute **expected**, **detected**, **correct**, **missing**, and **ambiguous** from attributes vs DOM/text/alt/title evidence.
- Prefer DOM evidence; OCR is an optional fallback layer (disabled by default).
- Store append-only rows in `badges` with raw badge text, normalized `badge_code`, relevance flag, and status notes.
- Sponsored promotional badges are treated as contextual (not automatically “good” or “bad”).

## Homepage banner tracking

Homepage Banner Tracking is a **retailer homepage-level** observation stream. It is independent of the product universe used by Share of Shelf.

Implemented in `collector/banners/` + `analytics/banner_share/`. Observations append to `banner_observations` (never overwritten).

### Banner definition

A **banner** is a visually distinct promotional/marketing component on the homepage, such as:

- Hero banner
- Promotional carousel slide
- Brand promotional tile
- Promotional graphic
- Brand marketing section

**Not** banners (excluded):

- Product cards / individual product listings
- Navigation or category links
- Footer content
- Search results
- Normal specification / product body text

Only content that is actually functioning as a homepage promotional/banner element is counted. Detection does **not** invent, infer, or fabricate banners.

### Evidence priority

1. DOM structure (candidate selection)
2. Visible text
3. Accessible labels (`aria-label`)
4. `alt`
5. `title`

OCR is disabled by default (`config/banners.yaml` → `detection.ocr_fallback_enabled: false`) and must never override confident DOM/text evidence.

### Brand detection

Tracked brands only: **Intel, AMD, Qualcomm, Apple** (token-boundary matching).

If a promotional banner is observed but the brand cannot be identified confidently → store `UNKNOWN` or `AMBIGUOUS` (conflicting brands). Never guess.

### Banner Share

```
Banner Share(brand) =
  brand banner observations /
  total tracked-brand banner observations
```

- Denominator = observations with a tracked brand (Intel/AMD/Qualcomm/Apple)
- `UNKNOWN` / `AMBIGUOUS` are **stored** for auditability but **excluded** from the denominator unless `include_unknown_in_banner_share: true`
- Do **not** use Share of Shelf product counts as the Banner Share denominator

Supports slices by brand, retailer, date, and historical daily trends (`banner_share_trends`).

**Config:** `config/banners.yaml`  
**Cadence:** at least 1× per day (`config/retailers.yaml` → `collection.banners_per_day`)

## Share of Shelf (SoS)

**Operational definition (assumed):**

```
SoS(brand) =
  eligible deduplicated tracked gaming listings for brand
  / total eligible deduplicated tracked gaming listings
```

Implemented in `analytics/share_of_shelf/`. Inclusion rules id: `sos_universe_v1`.

**Inclusion rules (consistent product universe):**

| Rule | Behavior |
|---|---|
| Product types | Only `notebook`, `desktop`, `workstation`, `tablet`, `cpu`, `gpu` |
| Accessories | Excluded via `excluded_categories` / ineligible or `UNKNOWN` type — **never** enter the denominator |
| Gaming eligibility | Title or category must match `share_of_shelf.gaming_signals` |
| Dedup | One row per `(retailer_code, country_code, retailer_sku)` |
| Availability | Out-of-stock **included** (listing visibility); availability tracked separately |
| Brand attribution | Uses `brand` only — each product counted once |
| Apple Brand+OEM | `brand=Apple` and `oem=Apple` still counts **once** toward Brand=Apple (OEM is not added into the brand numerator) |

**Supported slices:** retailer, country, product type, OEM filter / OEM drilldown, `as_of` datetime, and historical daily trends (`share_of_shelf_trends`).

**Config:** `config/product_types.yaml` → `share_of_shelf`, `excluded_categories`

## Share of Voice (SoV) / Search Visibility

Because the brief does not supply keywords, SoV uses a **configurable keyword set** in `config/keywords.yaml` (implementation assumption — not supplied by Bridge AI). Keywords are retailer- and country-specific and editable without code changes.

Implemented in `collector/search/` + `analytics/share_of_voice/`. Observations append to `search_observations` (never overwritten).

### Search-result definition

A search-result observation is one ranked product slot returned for a keyword query on a retailer homepage/search SERP, including page number and absolute position.

Identity preference: retailer SKU / product URL path ID, then URL — **not** title alone.

### Pagination

- Pagination is explicit (`config/keywords.yaml` → `pagination.max_pages`, `max_results_per_keyword`).
- Each accessed page number is stored.
- Collection stops when no next page exists, max pages/results is reached, or pagination becomes unreliable.
- Page 1 is **never** assumed to be the full result set.

### Completeness

| Status | Meaning |
|---|---|
| `COMPLETE` | Pages collected reliably through natural end or configured cap with reliable pagination signals |
| `PARTIAL` | Some results collected but pagination incomplete/unreliable (e.g. max pages hit with more available, bot interruption mid-run) |
| `FAILED` | Search could not be collected |
| `ZERO_RESULTS` | Search succeeded but returned no product results |

**Exact Share of Voice** is only claimed for `COMPLETE` collections (`require_complete=True` or `collection_basis=exact`). Otherwise label metrics as **observed / partial search visibility**.

### Metrics

- **Brand Presence** — brand appears ≥1 time in the (latest) result set
- **Appearances** — count of search-result observations for the brand (deduped by position within a search batch)
- **Top-N** — configurable (`top_n_options`: 3, 5, 10, 20); `top_n_count` / presence within positions ≤ N
- **Average ranking** — mean of **observed** positions only (missing brands are not rank 0)
- **Share of Voice** =

```
brand appearances / total tracked-brand appearances
```

Tracked brands: Intel, AMD, Qualcomm, Apple. **UNKNOWN is excluded from the denominator** unless `include_unknown_in_sov_denominator: true`.

### Historical trends

Daily SoV / appearances / top-N / average rank by retailer, country, keyword, brand from append-only observations.

**Config:** `config/keywords.yaml`  
**Commands:** `python -m collector.search.run` · `python -m analytics.share_of_voice.run_existing`

## Historical data

- Every collection creates timestamped observations.
- Prior rows are never updated in place for observational facts.
- `products` may hold the latest identity attributes for convenient joins; history lives in snapshot/history tables.
- Trends are computed by aggregating observation tables over time.

## SKU Explorer

Dashboard drill-down by retailer SKU showing identity, latest price/promo, audit history, badges, and observation timeline.

## Reports

Excel/CSV generation via Pandas (and openpyxl for Excel) from analytics outputs into `reports/`.

## Screenshots

Captured selectively for listing audits, product-page audits, and homepage banners. Not required for every price tick if impractical.
