"""Unit tests for Overall Brand Compliance Score analytics."""

from __future__ import annotations

import pytest

from analytics.compliance import (
    STRATEGY_CONFIGURED,
    STRATEGY_EQUAL,
    STRATEGY_POOLED,
    AuditScoreRow,
    ComplianceScoreConfig,
    compute_brand_scores,
    compute_compliance_score,
    compute_country_scores,
    compute_retailer_scores,
    compute_segment_score,
    load_compliance_score_config,
)
from analytics.compliance.config import CHECK_CODES


def _row(
    *,
    brand: str = "Intel",
    retailer_code: str = "newegg",
    country_code: str = "US",
    product_type: str = "notebook",
    check_code: str = "S1",
    result: str = "PASS",
) -> AuditScoreRow:
    return AuditScoreRow(
        brand=brand,
        retailer_code=retailer_code,
        country_code=country_code,
        product_type=product_type,
        check_code=check_code,
        result=result,
    )


def _all_checks(
    result: str,
    *,
    product_type: str,
    brand: str = "Intel",
    retailer_code: str = "newegg",
    country_code: str = "US",
) -> list[AuditScoreRow]:
    return [
        _row(
            brand=brand,
            retailer_code=retailer_code,
            country_code=country_code,
            product_type=product_type,
            check_code=code,
            result=result,
        )
        for code in CHECK_CODES
    ]


def _cfg(**overrides: object) -> ComplianceScoreConfig:
    base = ComplianceScoreConfig(
        strategy=STRATEGY_EQUAL,
        check_weights={code: 1 / 7 for code in CHECK_CODES},
        segment_weights={"notebook": 0.85, "desktop": 0.15},
        weighted_product_types=frozenset({"notebook", "desktop"}),
        exclude_unknown_from_denominator=True,
    )
    data = {
        "strategy": base.strategy,
        "check_weights": dict(base.check_weights),
        "segment_weights": dict(base.segment_weights),
        "weighted_product_types": base.weighted_product_types,
        "exclude_unknown_from_denominator": base.exclude_unknown_from_denominator,
    }
    data.update(overrides)
    return ComplianceScoreConfig(**data)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Config / brief weights
# ---------------------------------------------------------------------------


def test_load_config_uses_brief_segment_weights() -> None:
    cfg = load_compliance_score_config()
    assert cfg.segment_weights["notebook"] == 0.85
    assert cfg.segment_weights["desktop"] == 0.15
    assert cfg.strategy in {
        STRATEGY_EQUAL,
        STRATEGY_CONFIGURED,
        STRATEGY_POOLED,
    }
    assert cfg.weighted_product_types == frozenset({"notebook", "desktop"})


# ---------------------------------------------------------------------------
# Segment scores
# ---------------------------------------------------------------------------


def test_notebook_segment_all_pass_equals_one() -> None:
    rows = _all_checks("PASS", product_type="notebook")
    segment = compute_segment_score(rows, "notebook", config=_cfg())
    assert segment.score == pytest.approx(1.0)
    assert segment.included_in_overall_weighting is True
    assert segment.segment_weight == 0.85
    assert segment.coverage.pass_count == 7
    assert segment.coverage.fail_count == 0


def test_desktop_segment_mixed_pass_fail() -> None:
    # 4 PASS + 3 FAIL across the seven checks → equal strategy mean = 4/7
    rows = [
        _row(product_type="desktop", check_code=code, result=result)
        for code, result in zip(
            CHECK_CODES,
            ["PASS", "PASS", "PASS", "PASS", "FAIL", "FAIL", "FAIL"],
        )
    ]
    segment = compute_segment_score(rows, "desktop", config=_cfg())
    assert segment.score == pytest.approx(4 / 7)
    assert segment.segment_weight == 0.15


def test_unknown_excluded_from_check_pass_rate() -> None:
    rows = [
        _row(check_code="S1", result="PASS"),
        _row(check_code="S1", result="FAIL"),
        _row(check_code="S1", result="UNKNOWN"),
        _row(check_code="S1", result="UNKNOWN"),
    ]
    segment = compute_segment_score(rows, "notebook", config=_cfg())
    assert segment.check_scores["S1"].score == pytest.approx(0.5)
    assert segment.check_scores["S1"].coverage.unknown_count == 2
    assert segment.check_scores["S1"].coverage.coverage_rate == pytest.approx(0.5)


def test_equal_strategy_ignores_checks_with_only_unknown() -> None:
    rows = [
        _row(check_code="S1", result="PASS"),
        _row(check_code="S2", result="UNKNOWN"),
    ]
    segment = compute_segment_score(rows, "notebook", config=_cfg(strategy=STRATEGY_EQUAL))
    # Only S1 has a scored result → segment score = 1.0
    assert segment.score == pytest.approx(1.0)
    assert segment.check_scores["S2"].score is None


# ---------------------------------------------------------------------------
# Final weighted score (Notebook 85% + Desktop 15%)
# ---------------------------------------------------------------------------


def test_final_weighted_score_matches_brief_formula() -> None:
    rows = (
        _all_checks("PASS", product_type="notebook")
        + _all_checks("FAIL", product_type="desktop")
    )
    score = compute_compliance_score(rows, brand="Intel", config=_cfg())
    # notebook=1.0, desktop=0.0 → 0.85*1 + 0.15*0 = 0.85
    assert score.notebook is not None and score.notebook.score == pytest.approx(1.0)
    assert score.desktop is not None and score.desktop.score == pytest.approx(0.0)
    assert score.overall_score == pytest.approx(0.85)
    assert score.notebook_weight == 0.85
    assert score.desktop_weight == 0.15


def test_final_weighted_score_partial_segment_rates() -> None:
    notebook_rows = [
        _row(product_type="notebook", check_code=code, result="PASS")
        for code in CHECK_CODES
    ]
    desktop_rows = [
        _row(product_type="desktop", check_code=code, result=result)
        for code, result in zip(
            CHECK_CODES,
            ["PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL"],
        )
    ]
    score = compute_compliance_score(
        notebook_rows + desktop_rows, brand="Intel", config=_cfg()
    )
    expected = (1.0 * 0.85) + ((6 / 7) * 0.15)
    assert score.overall_score == pytest.approx(expected)


def test_overall_null_when_desktop_missing() -> None:
    rows = _all_checks("PASS", product_type="notebook")
    score = compute_compliance_score(rows, brand="Intel", config=_cfg())
    assert score.notebook is not None and score.notebook.score == pytest.approx(1.0)
    assert score.desktop is not None and score.desktop.score is None
    assert score.overall_score is None


def test_overall_null_when_notebook_missing() -> None:
    rows = _all_checks("PASS", product_type="desktop")
    score = compute_compliance_score(rows, brand="Intel", config=_cfg())
    assert score.notebook is not None and score.notebook.score is None
    assert score.desktop is not None and score.desktop.score == pytest.approx(1.0)
    assert score.overall_score is None


# ---------------------------------------------------------------------------
# Excluded product types (Workstation / Tablet / CPU / GPU)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("product_type", ["workstation", "tablet", "cpu", "gpu"])
def test_excluded_types_not_in_overall_weighting(product_type: str) -> None:
    rows = (
        _all_checks("PASS", product_type="notebook")
        + _all_checks("PASS", product_type="desktop")
        + _all_checks("FAIL", product_type=product_type)
    )
    score = compute_compliance_score(rows, brand="Intel", config=_cfg())
    assert score.overall_score == pytest.approx(1.0)
    assert product_type in score.other_segments
    other = score.other_segments[product_type]
    assert other.score == pytest.approx(0.0)
    assert other.included_in_overall_weighting is False
    assert other.segment_weight is None


def test_workstation_alone_does_not_produce_overall() -> None:
    rows = _all_checks("PASS", product_type="workstation")
    score = compute_compliance_score(rows, brand="Intel", config=_cfg())
    assert score.overall_score is None
    assert "workstation" in score.other_segments


# ---------------------------------------------------------------------------
# Configurable check aggregation strategies
# ---------------------------------------------------------------------------


def test_pooled_strategy_does_not_use_per_check_weights() -> None:
    # 2 PASS on S1, 1 FAIL on S2 → pooled = 2/3 (not mean of 1.0 and 0.0)
    rows = [
        _row(check_code="S1", result="PASS"),
        _row(check_code="S1", result="PASS"),
        _row(check_code="S2", result="FAIL"),
    ]
    equal = compute_segment_score(rows, "notebook", config=_cfg(strategy=STRATEGY_EQUAL))
    pooled = compute_segment_score(
        rows, "notebook", config=_cfg(strategy=STRATEGY_POOLED)
    )
    assert equal.score == pytest.approx(0.5)
    assert pooled.score == pytest.approx(2 / 3)


def test_configured_weights_strategy() -> None:
    weights = {code: 0.0 for code in CHECK_CODES}
    weights["S1"] = 1.0  # only S1 matters
    rows = [
        _row(check_code="S1", result="PASS"),
        _row(check_code="S2", result="FAIL"),
        _row(check_code="P1", result="FAIL"),
    ]
    segment = compute_segment_score(
        rows,
        "notebook",
        config=_cfg(strategy=STRATEGY_CONFIGURED, check_weights=weights),
    )
    assert segment.score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Brand / retailer / country rollups
# ---------------------------------------------------------------------------


def test_brand_level_scores() -> None:
    rows = (
        _all_checks("PASS", product_type="notebook", brand="Intel")
        + _all_checks("PASS", product_type="desktop", brand="Intel")
        + _all_checks("FAIL", product_type="notebook", brand="AMD")
        + _all_checks("FAIL", product_type="desktop", brand="AMD")
    )
    by_brand = compute_brand_scores(rows, config=_cfg())
    assert set(by_brand) == {"AMD", "Intel"}
    assert by_brand["Intel"].overall_score == pytest.approx(1.0)
    assert by_brand["AMD"].overall_score == pytest.approx(0.0)
    assert by_brand["Intel"].brand == "Intel"


def test_retailer_level_scores() -> None:
    rows = (
        _all_checks(
            "PASS",
            product_type="notebook",
            retailer_code="newegg",
        )
        + _all_checks(
            "PASS",
            product_type="desktop",
            retailer_code="newegg",
        )
        + _all_checks(
            "FAIL",
            product_type="notebook",
            retailer_code="mercadolibre",
            country_code="BR",
        )
        + _all_checks(
            "FAIL",
            product_type="desktop",
            retailer_code="mercadolibre",
            country_code="BR",
        )
    )
    by_retailer = compute_retailer_scores(rows, brand="Intel", config=_cfg())
    assert set(by_retailer) == {"mercadolibre", "newegg"}
    assert by_retailer["newegg"].overall_score == pytest.approx(1.0)
    assert by_retailer["mercadolibre"].overall_score == pytest.approx(0.0)
    assert by_retailer["newegg"].retailer_code == "newegg"
    assert by_retailer["newegg"].brand == "Intel"


def test_country_level_scores() -> None:
    rows = (
        _all_checks(
            "PASS",
            product_type="notebook",
            country_code="US",
            retailer_code="newegg",
        )
        + _all_checks(
            "PASS",
            product_type="desktop",
            country_code="US",
            retailer_code="newegg",
        )
        + _all_checks(
            "FAIL",
            product_type="notebook",
            country_code="BR",
            retailer_code="mercadolibre",
        )
        + _all_checks(
            "FAIL",
            product_type="desktop",
            country_code="BR",
            retailer_code="mercadolibre",
        )
    )
    by_country = compute_country_scores(rows, brand="Intel", config=_cfg())
    assert set(by_country) == {"BR", "US"}
    assert by_country["US"].overall_score == pytest.approx(1.0)
    assert by_country["BR"].overall_score == pytest.approx(0.0)
    assert by_country["US"].country_code == "US"


def test_scope_filter_isolates_brand() -> None:
    rows = (
        _all_checks("PASS", product_type="notebook", brand="Intel")
        + _all_checks("PASS", product_type="desktop", brand="Intel")
        + _all_checks("FAIL", product_type="notebook", brand="AMD")
        + _all_checks("FAIL", product_type="desktop", brand="AMD")
    )
    intel = compute_compliance_score(rows, brand="Intel", config=_cfg())
    amd = compute_compliance_score(rows, brand="AMD", config=_cfg())
    assert intel.overall_score == pytest.approx(1.0)
    assert amd.overall_score == pytest.approx(0.0)
