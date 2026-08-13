CURRENT COMPLIANCE SCORE (for the 1st check)

========================

Scoring strategy:

equal_check_weights

(interim default - Bridge AI has not specified individual S1-P5 weights;

 see docs/[clarifications.md](http://clarifications.md) C1)

VALIDATION (before scoring)

---

Products (total):                         21

Audit observations (total):               140

PASS:                                     123

FAIL:                                     1

UNKNOWN:                                  16

Products with any audit:                  20

Products with scored data (PASS/FAIL):    20

Products with scored notebook/desktop:    20

Products excluded from 85/15 overall:     1

Exclusion reasons (product counts):

- no_audit_records: 1

Audit counts by product_type:

  notebook: PASS=123 FAIL=1 UNKNOWN=16 scored_products=20

Products:

21

Audit observations:

PASS    = 123

FAIL    = 1

UNKNOWN = 16

Notebook Score:    99.29%

Desktop Score:     n/a

Overall Score:

(Notebook x 0.85) + (Desktop x 0.15)

= n/a  (both Notebook and Desktop scored segments required)

Brand Scores:

AMD = n/a

  notebook=97.14% desktop=n/a

Intel = n/a

  notebook=100.00% desktop=n/a

Retailer Scores:

newegg = n/a

  notebook=99.29% desktop=n/a

Country Scores:

US = n/a

  notebook=99.29% desktop=n/a

Other Segments:

Workstation  = n/a

Tablet       = n/a

CPU          = n/a

GPU          = n/a

Note:

Other segments are NOT included in the 85/15 overall score.

UNKNOWN results are excluded from score denominators (not treated as FAIL).

**Why overall is n/a:** All 140 audits are `product_type=notebook`. With no Desktop scored data, the brief formula cannot be applied (clarifications C2 — do not renormalize 85/15 onto Notebook alone).

**Reproduce:**

python -m [analytics.compliance.run](http://analytics.compliance.run)_existing

Flow: `ObservationRepository.list_audits()` → `AuditScoreRow` → `compute_compliance_score` / `compute_brand_scores` / `compute_retailer_scores` / `compute_country_scores`, using `config/compliance.yaml` (`equal_check_weights`, notebook=0.85, desktop=0.15).

## SHARE OF SHELF (existing database)

==================================

Inclusion rules id: sos_universe_v1

INVENTORY

---

Products (total / active):     21 / 21

Product snapshots:             25

Eligible universe size:        21

Excluded from universe:        0

  (all active products passed type + gaming-signal rules)

Brand Share of Shelf:

  Intel      15 / 21 = 71.43%

  AMD         5 / 21 = 23.81%

  UNKNOWN     1 / 21 =  4.76%   (sku=PL)

OEM drilldown (same universe; Apple Brand+OEM N/A — no Apple rows):

  MSI      8 = 38.10%

  Asus     4 = 19.05%

  UNKNOWN  3 = 14.29%

  Dell     2 =  9.52%

  HP       2 =  9.52%

  Acer     1 =  4.76%

  Lenovo   1 =  4.76%

Retailer: newegg only → same as overall (21)

Country:  US only     → same as overall (21)

Product type: notebook only → same as overall (21)

OEM-filtered brand SoS:

  Acer   Intel 100% (1)

  Asus   AMD 50% / Intel 50% (4)

  Dell   Intel 100% (2)

  HP     Intel 100% (2)

  Lenovo AMD 100% (1)

  MSI    Intel 87.50% / AMD 12.50% (8)

Historical trends (from product_snapshots):

  2026-08-11  universe=21

```
Intel 71.43% | AMD 23.81% | UNKNOWN 4.76%
```



### Reproduce :

python -m analytics.share_of_[shelf.run](http://shelf.run)_existing

## Homepage Banner Tracking

========================

Retailers configured:       2

Homepages inspected:        2

Successful inspections:     2

Failed inspections:         0

Total banner observations:  55

Intel:                      2

AMD:                        1

Qualcomm:                   0

Apple:                      0

Unknown/Ambiguous:          52

Database persistence:       PASS

Historical storage:         PASS (append-only banner_observations)

Banner Share calculation:   PASS

Tests:                      9 passed (tests/test_homepage_[banners.py](http://banners.py))

Screenshots/evidence:       2

  data/screenshots/20260812T160344Z_homepage_newegg.png

  data/screenshots/20260812T160400Z_homepage_mercadolibre.png

### **Banner Share (tracked-brand denominator only)**

Tracked denominator = 3  (UNKNOWN excluded)

Intel  2 / 3 = 66.67%

AMD    1 / 3 = 33.33%

Qualcomm / Apple = 0%

### **Real observed evidence (examples)**

- **AMD** — “Combo up savings… AMD Ryzen 7 7700X3D…” — link present — `detection_method=text`
- **Intel** — “Shell Shocker… 27 % off… Intel Core…” — discount `27 % off` — link present
- **UNKNOWN** — “Smooth Multitasking” / “The best deals in gaming” (promo surface, no confident brand)



### **Limitations / assumptions (documented)**

- Banner definition + Banner Share rules: `docs/methodology.md`, `config/banners.yaml`
- OCR disabled; DOM → text → aria → alt → title only
- UNKNOWN/AMBIGUOUS stored but **not** in Banner Share denominator
- Independent of Share of Shelf / `products` table
- High UNKNOWN on Mercado Libre is expected when carousel copy has no Intel/AMD/Qualcomm/Apple tokens — brands are not guessed
- Some Newegg homepage swipers are promotional combo tiles; product cards/nav/footer are excluded by selector rules



## Share of Voice / Search Visibility

==================================

Retailers:                 2 (newegg, mercadolibre)

Countries:                 2 (US, BR)

Keywords configured:       11 (config/keywords.yaml)

Searches attempted:        6  (smoke run: --limit-per-retailer 3)

Complete searches:         3  (newegg × 3 keywords)

Partial searches:          0

Failed searches:           0

Zero-result searches:      3  (mercadolibre × 3 — extraction returned no cards)

Result observations:       180

Unique products (sku):     149

Tracked-brand results:     175

UNKNOWN results:           5

Brand Presence:

Intel       present

AMD         present

Qualcomm    absent

Apple       absent

Appearances:

Intel       104

AMD         71

Qualcomm    0

Apple       0

Top-N:

Top 3:   Intel 6 | AMD 3 | Qualcomm 0 | Apple 0

Top 5:   Intel 11 | AMD 4 | Qualcomm 0 | Apple 0

Top 10:  Intel 21 | AMD 9 | Qualcomm 0 | Apple 0

Top 20:  Intel 36 | AMD 24 | Qualcomm 0 | Apple 0

Average Ranking (observed only):

Intel       30.02 (n=104)

AMD         30.28 (n=71)

Qualcomm    n/a

Apple       n/a

Share of Voice (tracked-brand denominator; UNKNOWN excluded):

Intel       59.43%

AMD         40.57%

Qualcomm     0.00%

Apple        0.00%

Database persistence: PASS (search_observations, append-only)

Tests: 9 passed (tests/test_share_of_[voice.py](http://voice.py))

### Reproduce:



# Collection (no product pipeline changes)

python -m [collector.search.run](http://collector.search.run)

python -m [collector.search.run](http://collector.search.run) --limit-per-retailer 3

# Analytics

python -m analytics.share_of_[voice.run](http://voice.run)_existing

### **Artifacts**

1. **Table:** `search_observations` (+ migration `0004_search_visibility_fields`)
2. **Keywords:** `config/keywords.yaml` (retailer × country × queries; editable without code)
3. **Docs:** `docs/methodology.md`, `docs/assumptions.md` (A12/A13)



### **Limitations**

- Mercado Libre returned **ZERO_RESULTS** (page loaded but card selectors found no items) — not claimed as SoV for BR.
- Newegg metrics are within the **first 60 results / up to 3 pages** config, not a claim of the full SERP.
- UNKNOWN excluded from SoV denominator by default.
- Independent of Share of Shelf product universe.



#### Single Prompt to run all the things....:

python -m [collector.run](http://collector.run) --all