# BridgeAI

**Retail Price, Promotion & Brand Positioning: Multi-Brand Comparison**

A production-style retail competitive intelligence platform that compares how major computing brands are priced, promoted, displayed, and positioned across tracked retail platforms.

> **Foundation phase status:** project structure, configuration, database models, and documentation are in place. Scrapers, dashboard UI, schedulers, and deployment are **not** implemented yet.

---

## Project objective

Build a reliable assessment solution that:

1. Collects real product data from tracked retailers
2. Tracks pricing and promotions multiple times per day
3. Runs retailer audits (S1, S2, P1–P5)
4. Computes an Overall Brand Compliance Score
5. Detects badges and homepage banners
6. Measures Share of Shelf and Share of Voice
7. Supports SKU drill-down, historical trends, Excel/CSV reports, and a Streamlit dashboard
8. Runs on automated schedules with clear, documented methodology

**Priorities:** correctness → complete required functionality → reliable real data → clear methodology → professional dashboard UX → deployment → optional extras.

**Hard rules:** never fabricate retailer data or analytical results; never hardcode secrets; document every implementation assumption in `docs/assumptions.md`.

---

## Scope

### Brands

Intel · AMD · Qualcomm · Apple

### OEMs

Dell · HP · Lenovo · Acer · Asus · MSI · Apple

Brand and OEM are independent attributes (e.g., ASUS + AMD Ryzen → Brand=AMD, OEM=Asus). Apple products use Brand=Apple and OEM=Apple. Standalone CPU/GPU components may have no OEM.

### Retailers

| Retailer | Country |
|---|---|
| Newegg | United States |
| Mercado Libre | Brazil |

### Product types (included)

Notebooks · Desktops · Workstations · Tablets · CPU/GPU (gaming segment)

### Excluded

Monitors · Keyboards · Cameras · Gift cards · Other accessories

---

## Architecture

Modular Python system with isolated retailer collectors:

- **Collection** — Playwright adapters under `collector/retailers/{newegg,mercadolibre}/`
- **Parsing / audit** — `collector/parsers/`, `collector/audit/`
- **Database** — SQLAlchemy models + PostgreSQL (`database/`)
- **Analytics** — compliance, SoS, SoV, trends, reports (`analytics/`)
- **Dashboard** — Streamlit + Plotly (`app/`)
- **Configuration** — YAML under `config/`
- **Tests** — pytest under `tests/`

Historical observations are **append-only** and always timestamped.

See `docs/architecture.md` for data flow, scheduling, and deployment design.

---

## Technology stack

| Layer | Technology |
|---|---|
| Language | Python |
| Collection | Playwright |
| Data | Pandas |
| ORM / DB | SQLAlchemy · PostgreSQL |
| Config | PyYAML · python-dotenv |
| Dashboard | Streamlit · Plotly |
| Tests | pytest |
| Deploy (planned) | Render Cron Jobs · Render PostgreSQL · Streamlit Community Cloud · GitHub |

---

## Project structure

```
BridgeAI/
├── app/                    # Streamlit dashboard (pages planned)
├── collector/
│   ├── retailers/
│   │   ├── newegg/
│   │   └── mercadolibre/
│   ├── parsers/
│   └── audit/
├── analytics/
│   ├── compliance/
│   ├── pricing/
│   ├── share_of_shelf/
│   ├── banner_share/
│   └── share_of_voice/
├── collector/
│   ├── banners/
│   ├── search/
├── database/
│   ├── models.py
│   ├── connection.py
│   └── repositories.py
├── config/
│   ├── brands.yaml
│   ├── oems.yaml
│   ├── retailers.yaml
│   ├── product_types.yaml
│   ├── keywords.yaml
│   ├── badges.yaml
│   └── compliance.yaml
├── tests/
├── docs/
│   ├── architecture.md
│   ├── methodology.md
│   ├── assumptions.md
│   └── clarifications.md
├── reports/
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Methodology summary

| Topic | Approach | Status |
|---|---|---|
| Product discovery | Category/search entry points; exclude accessories; dedupe by retailer SKU | Planned |
| Brand attribution | Processor → family → title → specs → description; else UNKNOWN | Assumed methodology |
| OEM attribution | Manufacturer field → title → URL → specs; NULL for components | Assumed methodology |
| Pricing | Native currency; 3×/day; append-only history | Planned |
| Audits S1–P5 | PASS / FAIL / UNKNOWN; UNKNOWN excluded from score denominator | Assumed + brief |
| Compliance score | Equal 1/7 check weights; Notebook 85% + Desktop 15% | Brief (segments) + assumed (checks) |
| Share of Shelf | Brand gaming listings / all eligible gaming listings | Assumed methodology |
| Share of Voice | Configurable keyword sets per retailer/country | Assumed methodology |
| Banners | At least daily homepage capture | Brief |
| Historical data | Timestamped append-only observations | Brief / design principle |

Full detail: `docs/methodology.md` · Assumptions: `docs/assumptions.md`

---

## Local setup

```bash
# 1. Clone / enter repo
cd BridgeAI

# 2. Create virtual environment
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# macOS / Linux
# source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Environment file (no real secrets in git)
copy .env.example .env   # Windows
# cp .env.example .env   # macOS / Linux

# 5. Local PostgreSQL 18 (Windows native, not Docker)
#    Copy .env.example to .env and set POSTGRES_PASSWORD.
#    Defaults: host=localhost port=5433 db=bridgeai user=postgres
#    Then: alembic upgrade head

# 6. Run foundation tests
pytest -q
```

Playwright browser binaries are only required when scrapers are implemented:

```bash
playwright install
```

---

## Planned deployment

| Component | Target |
|---|---|
| Collectors (3×/day + daily banners) | Render Cron Jobs |
| Database | Render PostgreSQL |
| Dashboard | Streamlit Community Cloud |
| Source | GitHub |

**Not deployed in this foundation phase.**

---

## Implementation status

### Implemented (foundation)

- [x] Project directory structure
- [x] Package scaffolding (`app`, `collector`, `analytics`, `database`, `tests`)
- [x] Configuration YAML (brands, OEMs, retailers, product types, keywords, badges, compliance)
- [x] `.env.example` and `.gitignore`
- [x] `requirements.txt`
- [x] SQLAlchemy models for all required entities + indexes
- [x] DB connection helpers and append-only repositories (no fake production data)
- [x] Alembic migrations (`alembic/versions/0001_initial_schema.py`)
- [x] Historical insert/query tests (`tests/test_database_layer.py`)
- [x] Architecture, methodology, and assumptions documentation
- [x] README
- [x] Foundation validation tests

### Database migrations

With PostgreSQL configured in `.env`:

```bash
alembic upgrade head
```

Offline SQL preview:

```bash
alembic upgrade head --sql
```

### Planned (next phases)

- [ ] Controlled real-data Newegg collection prototype
- [ ] Mercado Libre collector
- [ ] Parsing / normalization / brand-OEM attribution pipeline
- [ ] Audit evaluation engine (S1–P5)
- [x] Overall Brand Compliance Score (`analytics/compliance/`; Notebook 85% + Desktop 15%)
- [x] Pricing / promotion analytics (`analytics/pricing/`)
- [x] Share of Shelf analytics (`analytics/share_of_shelf/`)
- [x] Homepage banner tracking (`collector/banners/`) + Banner Share (`analytics/banner_share/`)
- [x] Share of Voice / search visibility (`collector/search/`, `analytics/share_of_voice/`)
- [ ] Analytics (trends, insights)
- [ ] Excel/CSV report generation
- [ ] Streamlit dashboard
- [ ] Automated scheduling on Render
- [ ] Deployment to Render + Streamlit Community Cloud

### Optional (after required system is stable)

- [ ] AI natural-language Q&A
- [ ] Alerts
- [ ] Competitiveness score
- [ ] Advanced automated insights

---

## License / assessment note

Built as a time-boxed interview assessment deliverable. Methodology assumptions are documented transparently in `docs/assumptions.md`.
