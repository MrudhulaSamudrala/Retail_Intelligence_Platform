# Clarifications Needed (Bridge AI)

Open questions where the project brief is silent or ambiguous. Until Bridge AI clarifies, the implementation uses **configurable** defaults documented here and in `docs/assumptions.md`. Defaults are **not** claimed to come from the brief.

---

## C1 — Individual weights for S1, S2, P1, P2, P3, P4, P5

**Status:** Unresolved

**Brief fact:** The overall Brand Compliance Score formula uses:

```
Overall = (Notebook_score × 0.85) + (Desktop_score × 0.15)
```

Notebook = 85% and Desktop = 15% are explicit. The brief does **not** specify how the seven audit checks (S1, S2, P1, P2, P3, P4, P5) combine into each segment score.

**What we must not do:** Invent permanent Bridge AI–authoritative individual weights for S1–P5.

**Interim approach:** `config/compliance.yaml` → `check_aggregation.strategy` is configurable:

| Strategy | Behavior |
|---|---|
| `equal_check_weights` | Interim default. Each check with scored data contributes equally (1/n). |
| `configured_check_weights` | Uses `check_weights` in the same YAML (placeholders until clarified). |
| `pooled_observations` | Single pass rate over all PASS/FAIL rows; no per-check weights. |

**Ask Bridge AI:** What relative weights (or other combination rule) apply to S1, S2, P1, P2, P3, P4, and P5 within Notebook and Desktop segment scores?

**Config:** `config/compliance.yaml` → `check_aggregation`, `check_weights`

---

## C2 — Missing Notebook or Desktop segment data

**Status:** Unresolved (implementation default chosen)

**Ambiguity:** If a scope has scored Notebook results but no scored Desktop results (or the reverse), how should the final weighted score be computed?

**Interim approach:** Require both segment scores to be defined before computing the final weighted score. If either segment has no scored (PASS/FAIL) data, `overall_score` is `null`; available segment scores are still reported. Do **not** silently renormalize 85/15 onto a single segment.

**Ask Bridge AI:** Confirm whether a single-segment scope should renormalize, remain null, or use another rule.

---

## Related resolved-from-brief items (not clarifications)

- Notebook / Desktop segment weights **0.85 / 0.15** — explicit in the brief; implemented as-is.
- Workstation, Tablet, CPU, GPU — **excluded** from the 85/15 overall weighting unless the project owner explicitly requires inclusion (`config/product_types.yaml` → `included_in_compliance_weighting`).
