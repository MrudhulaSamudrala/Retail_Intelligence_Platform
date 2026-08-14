# BridgeAI — Competitive Retail Analytics Platform

BridgeAI is a competitive retail analytics platform that monitors how major processor brands — **Intel, AMD, Qualcomm, and Apple** — are represented across supported online retailers (**Newegg** and **Mercado Libre**).

It collects product, pricing, visibility, compliance, badge, banner, and search data, stores observations in PostgreSQL, and converts them into an interactive Streamlit dashboard and downloadable Excel/PSV reports.

---

## Dashboard Preview

BridgeAI provides an interactive Streamlit dashboard for monitoring competitive retail presence, product data quality, share of shelf, and brand compliance.

| Retail Homepage Presence | Product Data Quality |
| --- | --- |
| <img src="assets/dashboard/Homepage_presence.png" alt="Retail Homepage Presence" width="450"> | <img src="assets/dashboard/Product_data_quality.png" alt="Product Data Quality" width="450"> |

| Share of Shelf | Brand Compliance |
| --- | --- |
| <img src="assets/dashboard/Shelf_presence.png" alt="Share of Shelf" width="450"> | <img src="assets/dashboard/Brand_compliance.png" alt="Brand Compliance" width="450"> |

---

## What BridgeAI Measures

| Area | What it provides |
| --- | --- |
| Product Data | Product, processor, GPU, RAM, storage, price, and other attributes |
| Share of Shelf | Brand share across eligible gaming products |
| Search Visibility | Brand presence and ranking for tracked search keywords |
| Pricing & Promotions | Product prices, discounts, and promotional observations |
| Brand Compliance | S1, S2, P1–P5 compliance checks |
| Badge Coverage | Brand-specific badge detection and coverage |
| Banner Tracking | Homepage brand banners, links, discounts, and badges |
| Product Explorer | SKU-level product investigation and traceability |

---

## Architecture

```text
                    BRIDGEAI
                       │
             ┌─────────┴─────────┐
             │                   │
        Data Collection       Configuration
             │
     ┌───────┼────────┬──────────────┐
     ↓       ↓        ↓              ↓
  Newegg    Mercado   Audits       Banners
            Libre
     │       │        │              │
     └───────┼────────┴──────────────┘
             ↓
        PostgreSQL
             │
             ↓
        Analytics Layer
             │
     ┌───────┼──────────┐
     ↓       ↓          ↓
 Dashboard  Reports   Historical Data
 Streamlit  Excel/PSV
```

---

## Data Collection

BridgeAI uses retailer-specific, browser-based collectors to observe supported retailer websites.

A full production run (`python -m collector.run --all`) covers:

1. Product collection from Newegg and Mercado Libre
2. Retailer page audits against the S1–P5 compliance checks
3. Brand badge detection
4. Pricing and discount snapshots
5. Homepage banner detection
6. Search visibility for configured keywords
7. Persistence of observations and collection metadata in PostgreSQL

### Collection Flow

A production collection runs the complete pipeline:

```text
Newegg + Mercado Libre
        ↓
Product collection
        ↓
Audits + Badges + Pricing
        ↓
Banners + Search Visibility
        ↓
PostgreSQL
        ↓
Analytics
        ↓
Streamlit Dashboard
        ↓
Excel + PSV Reports
```

All components in a full production run are associated with a single **collection run** for traceability.

---

## Dashboard

The Streamlit dashboard provides:

- Collection Status
- Executive overview and KPIs
- Shelf Presence (Share of Shelf)
- Search Visibility
- Pricing & Promotions
- Brand Compliance
- Homepage Presence (Banner Tracking)
- Product Data Quality
- Badge Coverage
- Product Explorer
- Historical Excel/PSV reports

The dashboard distinguishes **Unavailable ≠ Zero** and **Partial ≠ Complete**, so missing retailer data is not shown as a zero metric.

---

## Brand Compliance

Brand Compliance measures how clearly **Intel, AMD, Qualcomm, and Apple** are presented on retailer listing and product pages — titles, badges, spec tables, and rich media.

Each audited product is evaluated with **7 checks**. A check is **PASS**, **FAIL**, or **UNKNOWN** (evidence could not be determined). UNKNOWN is excluded from pass rates. If a brand has no scored evidence, the dashboard shows **N/A**, not 0%.

| Check | What is evaluated |
| --- | --- |
| S1 | Listing title includes the brand name and/or processor line |
| S2 | Brand badge is present on the listing tile |
| P1 | Product page title includes brand, processor line, or generation |
| P2 | Brand badge is present on the product page |
| P3 | Brand or processor line appears in the specification table |
| P4 | Brand-led rich media is present on the product page |
| P5 | OEM rich media is present on the product page |

Scored checks (PASS/FAIL) are weighted **equally**. The overall score uses the existing segment weights: **notebook 85%** and **desktop 15%**. Coverage is reported separately as scored observations / eligible observations.

The dashboard shows an overall summary, one card per brand (pass rate, PASS/FAIL counts, coverage), a check-by-check comparison, and the lowest-scoring checks where compliance is being lost.

---

## Data Interpretation

| Topic | How to read it |
| --- | --- |
| Collection scope | Results reflect only data successfully observed during each collection run. |
| Top 100 | Newegg results cover the configured top 100 products, not its complete catalog. |
| Brand absence | A brand not observed in the collected products does not imply the retailer does not sell it. |
| Partial coverage | Mercado Libre is currently PARTIAL; its metrics represent the successfully collected observations. |
| Share of Shelf | Represents brand presence within the collected eligible products, not total retailer or market share. |
| Data states | N/A, UNKNOWN, PARTIAL, and NOT AVAILABLE indicate different evidence/collection states and are not equivalent to zero. |
| Search Visibility | Based on configured keywords and successfully collected search results. |
| Historical Reports | Each report is scoped to its own collection run; missing components are not filled from other runs. |

---

## Automated Collection

Windows Task Scheduler can run the complete production pipeline three times per day in the **local Windows timezone**:

```text
08:00 · 14:00 · 20:00
```

```text
.venv\Scripts\python.exe -m collector.run --all
```

Each scheduled run: **Collection → PostgreSQL → Excel + PSV reports → Dashboard**. Overlapping production collections are blocked, and logs are written under `logs/collections/`.

Register the task from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows_scheduler.ps1
```

---

## Reports

Each production collection can generate **Excel** and **PSV** with the same run-scoped analytics:

Executive summary · Retailer coverage · Share of Shelf · Search Visibility · Pricing · Promotions · Brand Compliance · Banner Tracking · Badge Coverage · Product Data Quality · Product data

Historical reports are preserved by collection run under `reports/`.

---

## Tech Stack

| Layer | Technologies |
| --- | --- |
| Dashboard | Streamlit, Plotly |
| Data | Python, PostgreSQL, SQLAlchemy, Pandas |
| Collection | Playwright, retailer-specific collectors |
| Reports | Excel, PSV |
| Automation | Windows Task Scheduler, PowerShell |

---

## Current Limitations

BridgeAI is a working, production-oriented **local** system:

- Mercado Libre collection can be **PARTIAL** when pages or dynamically loaded content are not fully accessible.
- Search visibility can be **PARTIAL** depending on retailer/search-page availability.
- Banner detection uses stored page evidence; some banners remain `UNKNOWN` when brand evidence is insufficient.
- The scheduled collector runs on the configured Windows machine and its local browser/CDP setup.
- Cloud deployment is not finalized.
- Historical availability differs across collection runs because some components were added later.

The system reports these cases as **PARTIAL / NOT AVAILABLE** rather than fabricating values.

---

## Running Locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Dashboard:

```powershell
.\.venv\Scripts\python.exe -m streamlit run dashboard/app.py
```

Validate the pipeline (no live retailer collection):

```powershell
.\.venv\Scripts\python.exe -m collector.run --all --dry-run
```

Live production collection (accesses retailer websites):

```powershell
.\.venv\Scripts\python.exe -m collector.run --all
```

---

## Project Structure

```text
BridgeAI/
├── collector/       # Retailer data collection
├── analytics/       # Analytics and scoring
├── dashboard/       # Streamlit dashboard
├── reporting/       # Excel / PSV generation
├── database/        # PostgreSQL models and access
├── config/          # Retailer, scoring, and schedule configuration
├── scripts/         # Scheduler and automation scripts
├── tests/           # Automated tests
├── reports/         # Generated reports
├── requirements.txt
└── README.md
```

---

## Status

**Collect → Store → Analyze → Visualize → Report → Automate**

The current implementation focuses on gaming-product / processor-brand competitive intelligence across the supported retailer platforms.
