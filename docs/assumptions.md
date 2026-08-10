# Implementation Assumptions

This file records **implementation assumptions** made where the project brief did not specify a methodology, weighting, threshold, classification rule, or implementation detail.

Assumptions are chosen to be simple, defensible, and reproducible. They are labeled as implementation assumptions and are **not** claimed to come from Bridge AI unless the brief explicitly states them.

Where practical, values are externalized under `config/` so they can be changed without code changes.

---

## A1 — Equal audit check weights (S1, S2, P1–P5)

**Ambiguity:** Brief specifies Notebook=85% / Desktop=15% overall weighting but does not specify individual weights for S1, S2, P1, P2, P3, P4, P5.

**Assumption:** Each audit check weight = 1/7.

**Rationale:** No criterion is identified as more important; equal weighting is the most neutral and reproducible approach.

**Config:** `config/compliance.yaml` → `check_weights`

---

## A2 — Notebook / Desktop-only compliance segment weighting

**Ambiguity:** Whether Workstation, Tablet, CPU, GPU participate in the 85/15 overall score.

**Assumption:** Apply 85/15 only to Notebook and Desktop. Other product types may be audited and reported separately but are excluded from the Overall Brand Compliance Score denominator unless later required.

**Rationale:** Brief states Notebook and Desktop weights explicitly and does not mention other types in that formula.

**Config:** `config/product_types.yaml` → `included_in_compliance_weighting`; `config/compliance.yaml` → `segment_weights`

---

## A3 — UNKNOWN audit handling

**Ambiguity:** How to treat insufficient evidence or scraper failures in compliance scoring.

**Assumption:**

- Valid results: PASS, FAIL, UNKNOWN
- UNKNOWN is excluded from the score denominator
- Coverage / data completeness is reported separately
- UNKNOWN never auto-becomes PASS or FAIL

**Rationale:** A collection failure must not be interpreted as retailer non-compliance.

**Config:** `config/compliance.yaml` → `unknown_handling`

---

## A4 — Share of Shelf definition

**Ambiguity:** Brief requires Share of Shelf but does not define exact inclusion rules.

**Assumption:**

```
SoS(brand) = gaming-eligible deduplicated tracked listings for brand
           / all gaming-eligible deduplicated tracked listings
```

Within a retailer/country (and optional product-type slice):

- Include notebook, desktop, workstation, tablet, cpu, gpu
- Exclude accessories and irrelevant products
- Gaming eligibility via configurable title/category signals
- Deduplicate by `retailer_sku`
- Include out-of-stock listings (visibility metric); track availability separately

**Rationale:** SoS measures shelf/listing presence, not solely sellable inventory.

**Config:** `config/product_types.yaml` → `share_of_shelf`

---

## A5 — Brand attribution hierarchy

**Ambiguity:** Exact brand-resolution order when multiple signals conflict.

**Assumption:** Deterministic order:

1. Explicit processor/SoC manufacturer
2. Processor family
3. Product title
4. Specification table
5. Description

Else `UNKNOWN` (never invent a brand).

**Rationale:** Prefers structured technical identity over marketing copy.

**Config:** `config/brands.yaml` → `attribution`

---

## A6 — OEM attribution hierarchy and null OEM

**Ambiguity:** How to assign OEM; what to do for components.

**Assumption:** Order = explicit manufacturer field → title → URL/domain metadata → specs. Standalone CPU/GPU → `oem = NULL`.

**Rationale:** Components are not system OEMs; forcing an OEM would fabricate structure.

**Config:** `config/oems.yaml`

---

## A7 — Product type classification

**Ambiguity:** Borderline products (e.g., 2-in-1, mini-PC, Chromebook).

**Assumption:** Deterministic rules from category → title → specifications; if still ambiguous, `UNKNOWN` and preserve `category_raw`.

**Rationale:** Prefer incomplete classification over silent misclassification.

**Config:** `config/product_types.yaml`

---

## A8 — Out-of-stock inclusion in Share of Shelf

**Ambiguity:** Whether OOS listings count toward SoS.

**Assumption:** Include OOS listings in SoS; store availability separately.

**Rationale:** SoS measures retail listing visibility unless the brief says otherwise.

---

## A9 — No default currency conversion

**Ambiguity:** Whether to compare US and BR prices in one currency.

**Assumption:** Store retailer-native price and currency only. No FX conversion unless a specific cross-country view explicitly requires it later.

**Rationale:** Preserves observed retail reality; FX would introduce another assumption layer.

---

## A10 — Append-only historical observations

**Ambiguity:** Storage pattern for repeated collections.

**Assumption:** Every collection appends timestamped observations; prior observations are never overwritten. `products` may store latest identity fields for convenience.

**Rationale:** Required for reliable trends and auditability.

---

## A11 — Banner tracking cadence

**Ambiguity:** Brief requires daily banner tracking; pricing/audits are 3×/day.

**Assumption:** Banners run at least once per day. Extra banner captures during the 3× runs are allowed as additional observations but are not represented as a brief requirement.

**Config:** `config/retailers.yaml` → `collection.banners_per_day`

---

## A12 — Share of Voice keyword set

**Ambiguity:** Brief does not define keywords.

**Assumption:** Maintain a small, defensible gaming purchase-intent keyword set per retailer/country in `config/keywords.yaml` (EN for Newegg, PT-BR for Mercado Libre).

**Rationale:** Enables a working SoV metric aligned to tracked product scope.

**Important:** This keyword list is an **implementation assumption**. It was **not** supplied by Bridge AI.

**Config:** `config/keywords.yaml`

---

## A13 — Share of Voice formula

**Ambiguity:** Exact SoV calculation.

**Assumption:** For each keyword, use top N results (default 20). Brand SoV = share of result slots attributed to that brand, averaged across keywords. Store sponsored flags; default reporting prefers organic slots when identifiable.

**Config:** `config/keywords.yaml` → `share_of_voice`

---

## A14 — Audit check operational definitions

**Ambiguity:** Brief names S1, S2, P1–P5 but does not fully specify evaluation criteria.

**Assumption:** Use the operational definitions in `config/compliance.yaml` (search visibility, filter discoverability, title branding, spec accuracy, merchandising consistency, promo claim validity, badge relevance).

**Rationale:** Required to implement a complete audit pipeline; definitions are transparent and configurable.

**Config:** `config/compliance.yaml` → `audit_checks`

---

## A15 — Selective screenshots

**Ambiguity:** Whether every observation needs a screenshot.

**Assumption:** Capture screenshots for listing/product audits and homepage banners when useful; do not require screenshots for every price observation.

**Rationale:** Keeps collection practical under 3× daily cadence.

---

## A16 — Pricing collection schedule windows

**Ambiguity:** Exact clock times for 3× daily runs.

**Assumption:** UTC cron `0 2,10,18 * * *` for pricing/audits; banner cron `0 14 * * *`.

**Rationale:** Evenly spaced coverage across a day; adjustable in config.

**Config:** `config/retailers.yaml` → `scheduling`

---

## A17 — Competitiveness score deferred

**Ambiguity:** Optional metric definition.

**Assumption:** Do not implement until required metrics work. When added, use a transparent configurable formula documented in assumptions/methodology.

**Rationale:** Matches assessment priority order.

---

## A18 — Additional config files beyond the brief skeleton

**Ambiguity:** Brief skeleton lists five YAML files; OEM lists and compliance weights also need a home.

**Assumption:** Add `config/oems.yaml` and `config/compliance.yaml` for clarity and configurability.

**Rationale:** Avoids hardcoding OEMs and audit weights inside Python modules.
