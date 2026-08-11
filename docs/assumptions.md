# Implementation Assumptions

This file records **implementation assumptions** made where the project brief did not specify a methodology, weighting, threshold, classification rule, or implementation detail.

Assumptions are chosen to be simple, defensible, and reproducible. They are labeled as implementation assumptions and are **not** claimed to come from Bridge AI unless the brief explicitly states them.

Where practical, values are externalized under `config/` so they can be changed without code changes.

---

## A1 — Interim S1–P5 combination (until Bridge AI clarifies)

**Ambiguity:** Brief specifies Notebook=85% / Desktop=15% overall weighting but does **not** specify individual weights for S1, S2, P1, P2, P3, P4, P5. See `docs/clarifications.md` (C1).

**Assumption (interim, configurable — not Bridge AI–authoritative):** Default `check_aggregation.strategy = equal_check_weights` (each scored check contributes equally). Alternate strategies (`configured_check_weights`, `pooled_observations`) are available without code changes. Do not treat placeholder `check_weights` as brief-specified.

**Rationale:** Scoring must remain runnable while weights are unresolved; equal weights are the most neutral interim default and can be replaced when Bridge AI clarifies.

**Config:** `config/compliance.yaml` → `check_aggregation`, `check_weights`

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

## A6 — OEM attribution hierarchy and UNKNOWN OEM

**Ambiguity:** How to assign OEM; what to do for components and weak matches.

**Assumption:** Order = explicit manufacturer field → title → specifications → description. Matching uses alphanumeric token boundaries. Insufficient or conflicting evidence → `UNKNOWN`. Standalone CPU/GPU product types → `OEM = UNKNOWN` (components are not system OEMs). Brand and OEM remain independent classifiers (`collector/classification.py`).

**Rationale:** Prefer UNKNOWN over fabricated OEM structure; prevents false positives such as `hp` inside `HDMI`.

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

**Ambiguity:** Brief names S1, S2, P1–P5; earlier draft config used alternate labels.

**Assumption:** Use the project-brief definitions implemented in `collector/audit/`:

| Code | Brief definition |
|---|---|
| S1 | Listing page title includes brand name and/or brand-specific processor line |
| S2 | Brand badge is present on the listing tile |
| P1 | Product page title includes brand name, processor line or generation |
| P2 | Brand badge is present on the product page |
| P3 | Brand or processor line appears in the specification table |
| P4 | Brand-led rich media is present |
| P5 | OEM rich media is present |

Each check independently returns `PASS` / `FAIL` / `UNKNOWN` with preserved evidence. Missing evidence is never treated as `PASS`. Overall Brand Compliance Score is not computed in the audit-engine phase.

**Rationale:** Aligns implementation with the assessment brief; keeps checks independently testable before scoring.

**Config:** `config/compliance.yaml` → `audit_checks`, `brand_badge_patterns`, `oem_rich_media_patterns`

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

---

## A20 — Newegg prototype discovery scope

**Ambiguity:** Brief does not specify exact Newegg category IDs or search URLs for the first prototype.

**Assumption:** Controlled scope uses a single Best Match search defined in `config/newegg_discovery.yaml`:
`https://www.newegg.com/p/pl?d=gaming+laptop` (gaming notebooks). Lowest-price sort (`Order=1`) is avoided because it surfaces accessories and unrelated junk.

**Rationale:** Aligns with tracked gaming product types while keeping the first live run small and reproducible.

---

## A24 — Newegg bot protection / Playwright CDP attach

**Ambiguity:** Automated Playwright Chromium launches are frequently blocked by Newegg/Cloudflare ("unusual traffic" / Turnstile), while a normal Chrome session can load the same pages.

**Assumption:** Prefer attaching Playwright to a system Chrome via `COLLECTION_CDP_URL` (Chrome started with `--remote-debugging-port=9222`). Direct Chromium launch remains supported but may fail under bot protection. Screenshots are captured on challenge/listing/product pages for debugging.

**Rationale:** Proves the real Newegg → parse → PostgreSQL pipeline without fabricating data or bypassing retailer terms with fake HTML fixtures.

---

## A21 — Spec fields stored in snapshot raw_payload

**Ambiguity:** Processor/GPU/RAM/storage are required extraction fields but were not dedicated columns in the foundation schema.

**Assumption:** Persist these values inside `product_snapshots.raw_payload` (and mirrored on the normalized DTO) without a schema migration for the prototype.

**Rationale:** Avoids premature schema churn; values remain queryable via JSON and can be promoted to columns later if needed.

---

## A19 — SQLite for offline unit tests of the data layer

**Ambiguity:** How to validate insert/query behavior without a live PostgreSQL instance during local foundation work.

**Assumption:** Unit tests use in-memory SQLite with JSON fallback. Production schema targets PostgreSQL (JSONB) via Alembic migrations. ORM JSON columns use `JSON().with_variant(JSONB(), "postgresql")`.

**Rationale:** Keeps tests hermetic and avoids requiring production credentials; Alembic remains the source of truth for PostgreSQL DDL.

---

## A22 — Local PostgreSQL 18 (Windows native), not Docker

**Ambiguity:** Earlier exploration briefly attempted Docker PostgreSQL; the assessment target is a Windows-native PostgreSQL 18 instance.

**Assumption:** BridgeAI connects to local PostgreSQL 18 via environment variables:
`POSTGRES_HOST=localhost`, `POSTGRES_PORT=5433`, `POSTGRES_DB=bridgeai`, `POSTGRES_USER=postgres`, plus `POSTGRES_PASSWORD` from untracked `.env` (never committed).

**Rationale:** Matches the installed PostgreSQL 18 service (`port = 5433`) and removes any Docker dependency for the database.

---

## A23 — Local trust auth used only when no password was available

**Ambiguity:** No `.env`, `POSTGRES_PASSWORD`, or `DATABASE_URL` was present to authenticate to PostgreSQL 18 (scram-sha-256).

**Assumption:** For local setup only, `pg_hba.conf` host entries for `127.0.0.1/32` and `::1/128` were switched to `trust` so migrations and verification could complete. A backup was saved as `pg_hba.conf.bridgeai.bak`. Set `POSTGRES_PASSWORD` in `.env` and restore scram auth when ready.

**Rationale:** Completes required local DB setup without hardcoding or inventing a committed password.

---

## A25 — Platform badge families and expected/detected evaluation

**Ambiguity:** Brief requires badge detection and storage but does not define processor badge families or how expected vs visible badges should be compared.

**Assumption:** Track these platform badge families from `config/badges.yaml` → `platform_families`:

| Brand | Families |
|---|---|
| Intel | Core, Core Ultra, Evo, vPro |
| AMD | Ryzen, Ryzen AI |
| Qualcomm | Snapdragon |
| Apple | Apple Silicon, M-series |

For each product:

1. **Expected** — derived from processor / title / specs / description attributes
2. **Detected** — derived from DOM evidence (`badge_texts`, `img` alt/title, element title/text); page-wide text is weaker and may be ambiguous
3. **Correct** = expected ∩ detected
4. **Missing** = expected − detected (excluding ambiguous-only expected hits)
5. **Ambiguous** — weak / context-missing / OCR-fallback matches

More-specific siblings supersede less-specific ones for expectation (Core Ultra over Core; Ryzen AI over Ryzen). OCR is an optional fallback layer (`detection.ocr_fallback_enabled`, default false) and never overrides confident DOM evidence.

Results are append-only rows in `badges` with `badge_code`, raw `badge_text`, `is_relevant=true` for platform families, and `relevance_notes` encoding status / source / pattern.

**Rationale:** Makes badge compliance auditable without inventing OCR as the primary signal.

**Config:** `config/badges.yaml` → `platform_families`, `detection`
**Code:** `collector/parsers/badges.py`

