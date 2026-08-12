# BridgeAI Architecture

## Business purpose

BridgeAI is a production-style retail competitive intelligence platform for the assessment project **Retail Price, Promotion & Brand Positioning: Multi-Brand Comparison**.

It compares how major computing brands (**Intel, AMD, Qualcomm, Apple**) are priced, promoted, displayed, and positioned across tracked retail platforms (**Newegg US**, **Mercado Libre Brazil**), across OEMs (**Dell, HP, Lenovo, Acer, Asus, MSI, Apple**) and in-scope product types (**Notebooks, Desktops, Workstations, Tablets, gaming CPU/GPU components**).

The platform produces:

- timestamped product and price observations
- promotion and badge evidence
- retailer audit results (S1, S2, P1–P5)
- Overall Brand Compliance Score
- Share of Shelf and Share of Voice
- homepage banner tracking
- historical trends and Excel/CSV reports
- an online Streamlit dashboard with actionable insights

## Architecture overview

The system is modular. Retailer-specific collection logic is isolated; shared parsing, persistence, analytics, and presentation sit above it.

```
┌─────────────────────────────────────────────────────────────┐
│                     Streamlit Dashboard (app/)              │
│              Plotly charts · SKU Explorer · reports UI      │
└─────────────────────────────┬───────────────────────────────┘
                              │ reads
┌─────────────────────────────▼───────────────────────────────┐
│                     Analytics (analytics/)                  │
│  Compliance · SoS · SoV · trends · insights · Excel/CSV    │
└─────────────────────────────┬───────────────────────────────┘
                              │ queries
┌─────────────────────────────▼───────────────────────────────┐
│                PostgreSQL via SQLAlchemy (database/)        │
│   products · snapshots · prices · audits · banners · …      │
└─────────────────────────────┬───────────────────────────────┘
                              │ writes (append-only observations)
┌─────────────────────────────▼───────────────────────────────┐
│                      Collector (collector/)                 │
│  discovery · pricing · promotions · audits · banners · SoV  │
│                                                             │
│   retailers/newegg/     retailers/mercadolibre/             │
│   parsers/              audit/                              │
└─────────────────────────────┬───────────────────────────────┘
                              │ driven by
┌─────────────────────────────▼───────────────────────────────┐
│                 Configuration (config/*.yaml)               │
│   brands · oems · retailers · product types · keywords · …  │
└─────────────────────────────────────────────────────────────┘
```

## Data flow

1. **Schedule / manual trigger** starts a `collection_run` row.
2. **Retailer adapter** (Playwright) discovers eligible listings and product pages.
3. **Parsers** extract title, price, availability, specs, badges, and promotion text.
4. **Normalization** maps raw fields to Brand, OEM, product type, and currency-native price using deterministic rules from `config/`.
5. **Persistence** upserts product identity in `products` and **appends** observation rows (`product_snapshots`, `price_history`, `promotions`, `retailer_audits`, `badges`, `banner_observations`, `search_observations`).
6. **Analytics** computes compliance, Share of Shelf, Share of Voice, trends, and insights from historical tables.
7. **Dashboard / reports** present results; Excel/CSV export reads the same analytics layer.

Historical observations are never overwritten. Every observation has an `observed_at` timestamp.

## Collection

| Concern | Location | Notes |
|---|---|---|
| Newegg US | `collector/retailers/newegg/` | Retailer-specific selectors and navigation |
| Mercado Libre BR | `collector/retailers/mercadolibre/` | Retailer-specific selectors and navigation |
| Shared parsing | `collector/parsers/` | Price, availability, platform badge expected/detected evaluation |
| Audit evaluation | `collector/audit/` | S1, S2, P1–P5 → PASS / FAIL / UNKNOWN |

Collection cadence (planned):

- Pricing, promotions, and retailer audits: **3× per day**
- Homepage banners: **at least 1× per day** (brief requirement)
- Share of Voice searches: configurable; planned daily with the banner window

Screenshots are captured when they provide audit or banner evidence; not every observation requires a screenshot.

## Database

PostgreSQL is the system of record. SQLAlchemy models live in `database/models.py`.

Schema changes are managed with **Alembic** (`alembic/`, `alembic.ini`). The initial migration `0001_initial_schema` creates all nine core tables.

Apply migrations (local/Render PostgreSQL configured via `.env`):

```bash
alembic upgrade head
```

Core tables:

| Table | Role |
|---|---|
| `collection_runs` | Execution metadata for each run |
| `products` | Retailer-scoped product identity (latest attributes) |
| `product_snapshots` | Append-only full product observations |
| `price_history` | Append-only native-currency prices |
| `promotions` | Append-only promo observations |
| `retailer_audits` | Append-only S1/S2/P1–P5 results |
| `badges` | Append-only badge detections |
| `banner_observations` | Append-only homepage/campaign banners |
| `search_observations` | Append-only SoV search result rows |

Connection settings come from environment variables (see `.env.example`). Secrets are never hardcoded.

## Analytics

Planned modules under `analytics/`:

- Overall Brand Compliance Score (`analytics/compliance/`: Notebook 85% + Desktop 15%; configurable S1–P5 combination — see `docs/clarifications.md`)
- Pricing / promotion analytics (`analytics/pricing/`: avg/median by brand, discounts, time series, retailer/country/type comparisons)
- Homepage banner tracking (`collector/banners/`) and Banner Share (`analytics/banner_share/`; tracked-brand denominator; UNKNOWN excluded by default)
- Share of Voice / search visibility (`collector/search/`, `analytics/share_of_voice/`; configurable keywords; pagination completeness)
- Share of Shelf (`analytics/share_of_shelf/`: gaming-eligible universe; brand/OEM; retailer/country/type; historical trends)
- Historical price / promo / compliance trends
- Actionable insight generation
- Excel/CSV report builders writing to `reports/`

Optional later: competitiveness score, alerts, NL Q&A — only after required metrics are stable.

## Dashboard

Streamlit app package: `app/` with multipage support under `app/pages/`.

Planned pages (not built in foundation phase):

- Executive overview / compliance
- Share of Shelf & Share of Voice
- Pricing & promotions
- Banner tracker
- SKU Explorer
- Trends & reports download

## Scheduling

Target: **Render Cron Jobs** invoking collector entrypoints on a UTC schedule defined in `config/retailers.yaml`.

Foundation phase does **not** deploy schedulers.

## Deployment (planned)

| Component | Target |
|---|---|
| Scheduled collectors | Render Cron Jobs |
| Database | Render PostgreSQL |
| Dashboard | Streamlit Community Cloud |
| Source control | GitHub |

Foundation phase explicitly does **not** deploy.

## Configuration

All brands, OEMs, retailers, product types, keywords, badges, and compliance weights are externalized under `config/` as YAML. Collectors and analytics must load these files rather than hardcoding business lists.
