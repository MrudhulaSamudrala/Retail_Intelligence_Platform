# BridgeAI Methodology

This document describes the planned methodology for data collection and metrics. Items marked **assumed** are implementation assumptions documented in `docs/assumptions.md` and made configurable where practical.

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
- Store `price_amount`, optional `list_price`, `currency`, discount fields, and `is_on_promotion`.
- Preserve retailer-native currency (**USD** for Newegg, **BRL** for Mercado Libre).
- Do **not** convert currencies unless a specific cross-country comparison explicitly requires it.
- Append to `price_history`; never overwrite prior prices.

## Promotions

- Extract promo text, codes, discount value/unit, and visible deal windows when present.
- Link promotions to the product and collection run.
- Append to `promotions` on every observation where a promo signal is present (or explicitly absent, if collectors record negative evidence later).

## Retailer audits (S1, S2, P1–P5)

Checks are defined in `config/compliance.yaml`.

| Code | Focus |
|---|---|
| S1 | Search visibility of brand products |
| S2 | Sort/filter brand discoverability |
| P1 | Accurate product title branding |
| P2 | Specification accuracy |
| P3 | Image / merchandising consistency |
| P4 | Promotion claim validity |
| P5 | Badge / endorsement relevance |

**Results:** `PASS` | `FAIL` | `UNKNOWN`

- `PASS` = successful compliance
- `FAIL` = observed non-compliance
- `UNKNOWN` = insufficient evidence / collection failure

**UNKNOWN handling (assumed):** excluded from the compliance score denominator; coverage reported separately. UNKNOWN never auto-converts to PASS or FAIL.

Detailed check criteria beyond the brief are **implementation assumptions** and are configurable.

## Overall Brand Compliance Score

Per brand, per retailer/country, for a scoring window:

1. For each audit check, compute pass rate over non-UNKNOWN results.
2. Combine the seven checks with **equal weights** (1/7 each) — assumed, configurable.
3. Compute Notebook segment score and Desktop segment score separately.
4. Overall:

```
Overall = (Notebook_score × 0.85) + (Desktop_score × 0.15)
```

Notebook/Desktop segment weights come from the project brief.

Do **not** apply Notebook/Desktop weighting to Workstations, Tablets, CPU, or GPU unless explicitly required later.

## Badge detection and relevance

- Detect badge strings using patterns in `config/badges.yaml`.
- Store raw badge text, normalized `badge_code`, and relevance classification.
- Relevance rules are configurable; high-relevance examples include Best Seller, Exclusive, Limited Time, Rebate, Official Store.
- Sponsored badges are treated as contextual (not automatically “good” or “bad”).

## Homepage banner tracking

- Capture homepage (and optionally campaign/category landing) banners **at least once per day**.
- Record position, detected brand/OEM, headline, destination URL, tracked-brand flag, and screenshot when practical.
- If banners are cheap to capture during the 3× pricing runs, additional observations may be stored, but daily cadence is the brief requirement.

## Share of Shelf (SoS)

**Operational definition (assumed):**

```
SoS(brand) =
  eligible deduplicated tracked gaming listings for brand
  / total eligible deduplicated tracked gaming listings
```

**Inclusion rules:**

- In-scope product types only (notebook, desktop, workstation, tablet, cpu, gpu)
- Gaming-eligible via configured title/category signals (`config/product_types.yaml`)
- Accessories and irrelevant products excluded
- Deduplicate by retailer SKU within retailer/country scope
- Preserve retailer / country / product-type scope in reporting slices
- **Out-of-stock listings remain included** (SoS measures listing visibility); availability tracked separately

## Share of Voice (SoV)

Because the brief does not supply keywords, SoV uses a **configurable keyword set** in `config/keywords.yaml` (implementation assumption — not supplied by Bridge AI).

**Planned formula (assumed):**

For each keyword, take the top N organic results (default N=20). Brand SoV is the share of those result slots attributed to the brand, averaged across the keyword set for that retailer/country.

Sponsored vs organic is stored so organic-only views remain possible.

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
