CURRENT COMPLIANCE SCORE (for the 1st check)

========================

Scoring strategy:

equal_check_weights

(interim default - Bridge AI has not specified individual S1-P5 weights;

 see docs/[clarifications.md](http://clarifications.md) C1)

VALIDATION (before scoring)

---------------------------

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