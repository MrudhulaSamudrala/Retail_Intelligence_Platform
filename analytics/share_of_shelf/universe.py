"""Share of Shelf eligible-product universe and inclusion rules.

Inclusion rules (consistent product universe)
---------------------------------------------
A listing enters the SoS denominator when **all** of the following hold:

1. **Product type** is in ``share_of_shelf.eligible_product_types``
   (notebook, desktop, workstation, tablet, cpu, gpu by default).
2. **Not an accessory / excluded category** — ``product_type`` is not
   ``UNKNOWN``/empty for type purposes when category text matches
   ``excluded_categories`` (monitors, keyboards, accessories, etc.).
3. **Gaming-eligible** — title or category matches configured
   ``gaming_signals`` (title_keywords / category_keywords).
4. **Deduplicated** by ``(retailer_code, country_code, retailer_sku)`` —
   one row per retailer SKU identity (the ``products`` primary identity).
5. **Out-of-stock included** when ``include_out_of_stock: true`` (default).
6. Active products only for the live ``products`` table view
   (``is_active``); historical snapshot views use snapshot presence instead.

Brand vs OEM (Apple double-count guard)
---------------------------------------
Brand SoS attributes each eligible product to exactly one ``brand`` value.
``oem`` is ignored for brand numerators. An Apple MacBook with
``brand=Apple`` and ``oem=Apple`` contributes **1** to Brand=Apple, never 2.
OEM drilldown is a separate aggregation on the ``oem`` field over the same
universe (still one row per product).

Config: ``config/product_types.yaml`` → ``share_of_shelf``, ``excluded_categories``
Rules id: ``INCLUSION_RULES_ID`` (bump when semantics change).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional, Sequence

from collector.config_loader import load_brands, load_product_types

INCLUSION_RULES_ID = "sos_universe_v1"

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SosUniverseConfig:
    """Resolved SoS universe policy from YAML."""

    eligible_product_types: frozenset[str]
    excluded_categories: tuple[str, ...]
    title_keywords: tuple[str, ...]
    category_keywords: tuple[str, ...]
    include_out_of_stock: bool
    deduplicate_by: str
    tracked_brands: frozenset[str]
    inclusion_rules_id: str = INCLUSION_RULES_ID


@dataclass(frozen=True)
class EligibleListing:
    """One deduplicated listing inside the eligible SoS universe."""

    product_id: int
    retailer_code: str
    country_code: str
    retailer_sku: str
    brand: Optional[str]
    oem: Optional[str]
    product_type: Optional[str]
    title: Optional[str]
    category_raw: Optional[str]
    availability: Optional[str] = None


@lru_cache(maxsize=1)
def load_sos_universe_config() -> SosUniverseConfig:
    types_cfg = load_product_types()
    brands_cfg = load_brands()
    sos = types_cfg.get("share_of_shelf") or {}
    signals = sos.get("gaming_signals") or {}
    tracked = {
        str(item["name"])
        for item in brands_cfg.get("brands") or []
        if isinstance(item, dict) and item.get("name")
    }
    return SosUniverseConfig(
        eligible_product_types=frozenset(
            str(x) for x in (sos.get("eligible_product_types") or [])
        ),
        excluded_categories=tuple(
            str(x).lower() for x in (types_cfg.get("excluded_categories") or [])
        ),
        title_keywords=tuple(
            str(x).lower() for x in (signals.get("title_keywords") or [])
        ),
        category_keywords=tuple(
            str(x).lower() for x in (signals.get("category_keywords") or [])
        ),
        include_out_of_stock=bool(sos.get("include_out_of_stock", True)),
        deduplicate_by=str(sos.get("deduplicate_by") or "retailer_sku"),
        tracked_brands=frozenset(tracked),
    )


def is_accessory_excluded(
    *,
    product_type: Optional[str],
    category_raw: Optional[str],
    config: SosUniverseConfig | None = None,
) -> bool:
    """True when the listing is an accessory or otherwise outside eligible types."""
    cfg = config or load_sos_universe_config()
    ptype = (product_type or "").strip().lower()
    if not ptype or ptype == UNKNOWN.lower():
        # UNKNOWN type: still exclude if category clearly looks like an accessory.
        cat = (category_raw or "").lower()
        if any(ex in cat for ex in cfg.excluded_categories):
            return True
        return True  # unknown type is not in eligible_product_types
    if ptype not in cfg.eligible_product_types:
        return True
    cat = (category_raw or "").lower()
    if cat and any(ex in cat for ex in cfg.excluded_categories):
        # Eligible type code wins only when category is not accessory-dominated.
        # If category contains an excluded token and no eligible-type alias hint,
        # treat as accessory contamination.
        type_hints = {
            "notebook",
            "laptop",
            "desktop",
            "workstation",
            "tablet",
            "cpu",
            "gpu",
            "graphics",
        }
        if not any(h in cat for h in type_hints):
            return True
    return False


def is_gaming_eligible(
    *,
    title: Optional[str],
    category_raw: Optional[str],
    config: SosUniverseConfig | None = None,
) -> bool:
    """True when title or category matches configured gaming signals."""
    cfg = config or load_sos_universe_config()
    title_l = (title or "").lower()
    cat_l = (category_raw or "").lower()
    if any(k in title_l for k in cfg.title_keywords):
        return True
    if any(k in cat_l for k in cfg.category_keywords):
        return True
    return False


def evaluate_listing_eligibility(
    *,
    product_type: Optional[str],
    title: Optional[str],
    category_raw: Optional[str],
    availability: Optional[str] = None,
    config: SosUniverseConfig | None = None,
) -> tuple[bool, Optional[str]]:
    """Return (eligible, exclusion_reason)."""
    cfg = config or load_sos_universe_config()
    if is_accessory_excluded(
        product_type=product_type, category_raw=category_raw, config=cfg
    ):
        return False, "accessory_or_ineligible_type"
    if not is_gaming_eligible(title=title, category_raw=category_raw, config=cfg):
        return False, "non_gaming"
    if not cfg.include_out_of_stock and (availability or "").lower() in {
        "out_of_stock",
        "oos",
    }:
        return False, "out_of_stock"
    return True, None


def build_eligible_universe(
    candidates: Sequence[dict[str, Any]],
    *,
    config: SosUniverseConfig | None = None,
) -> tuple[list[EligibleListing], dict[str, int]]:
    """Filter and dedupe candidate listing dicts into the SoS universe.

    Each candidate must provide: product_id, retailer_code, country_code,
    retailer_sku, brand, oem, product_type, title, category_raw; optional
    availability.

    Dedup key: ``(retailer_code, country_code, retailer_sku)`` — first wins
    (callers should pass latest-first if replacing).
    """
    cfg = config or load_sos_universe_config()
    exclusions = {
        "accessory_or_ineligible_type": 0,
        "non_gaming": 0,
        "missing_identity": 0,
        "duplicate_sku": 0,
        "out_of_stock": 0,
    }
    seen: set[tuple[str, str, str]] = set()
    eligible: list[EligibleListing] = []

    for row in candidates:
        retailer = row.get("retailer_code")
        country = row.get("country_code")
        sku = row.get("retailer_sku")
        product_id = row.get("product_id")
        if not retailer or not country or not sku or product_id is None:
            exclusions["missing_identity"] += 1
            continue
        key = (str(retailer), str(country), str(sku))
        if key in seen:
            exclusions["duplicate_sku"] += 1
            continue

        ok, reason = evaluate_listing_eligibility(
            product_type=row.get("product_type"),
            title=row.get("title"),
            category_raw=row.get("category_raw"),
            availability=row.get("availability"),
            config=cfg,
        )
        if not ok:
            exclusions[reason or "non_gaming"] += 1
            continue

        seen.add(key)
        eligible.append(
            EligibleListing(
                product_id=int(product_id),
                retailer_code=str(retailer),
                country_code=str(country),
                retailer_sku=str(sku),
                brand=row.get("brand"),
                oem=row.get("oem"),
                product_type=row.get("product_type"),
                title=row.get("title"),
                category_raw=row.get("category_raw"),
                availability=row.get("availability"),
            )
        )
    return eligible, exclusions
