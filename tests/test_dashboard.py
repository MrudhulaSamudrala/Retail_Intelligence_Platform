"""Dashboard unit tests — no fake KPI values; uses SQLite fixtures + pure helpers.

Live PostgreSQL connectivity is probed optionally when DATABASE_URL / POSTGRES_*
are configured; otherwise connection probe is skipped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from dashboard.config import alert_thresholds, load_dashboard_config
from dashboard.filters import (
    DashboardFilters,
    clear_filters,
    default_filters,
    previous_period,
    to_pricing_scope,
    to_sos_scope,
    to_sov_scope,
)
from dashboard.queries.collection import count_tracked_products, load_collection_status
from dashboard.services import (
    _count_price_changes,
    build_alerts,
    metric_average_price,
    metric_share_of_shelf,
    metric_tracked_products,
)
from dashboard.utils.format import fmt_change, fmt_pct
from dashboard.utils.semantics import DataState, MetricValue
from dashboard.presentation import format_search_coverage, ranked_visibility_available
from database.models import Base, CollectionRunStep, PriceHistory
from database.repositories import CollectionRunRepository, ProductRepository


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _seed(session: Session) -> None:
    runs = CollectionRunRepository(session)
    products = ProductRepository(session)
    run = runs.start(retailer_code="newegg", country_code="US", run_type="combined")
    run.status = "partial"
    run.completed_at = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    session.flush()
    session.add(
        CollectionRunStep(
            collection_run_id=run.id,
            component="mercadolibre",
            status="PARTIAL",
            error_message="PDP account verification",
            records_processed=2,
        )
    )
    session.flush()

    t0 = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)
    p1 = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="SKU-1",
        canonical_url="https://example.com/1",
        title="Intel Notebook",
        brand="Intel",
        oem="ASUS",
        product_type="notebook",
        collection_run_id=run.id,
    )
    p2 = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="SKU-2",
        canonical_url="https://example.com/2",
        title="AMD Desktop",
        brand="AMD",
        oem="MSI",
        product_type="desktop",
        collection_run_id=run.id,
    )
    session.add_all(
        [
            PriceHistory(
                product_id=p1.id,
                collection_run_id=run.id,
                observed_at=t0,
                price_amount=Decimal("1000"),
                list_price=Decimal("1200"),
                currency="USD",
                discount_pct=Decimal("16.67"),
                is_on_promotion=True,
            ),
            PriceHistory(
                product_id=p1.id,
                collection_run_id=run.id,
                observed_at=t1,
                price_amount=Decimal("900"),
                list_price=Decimal("1200"),
                currency="USD",
                discount_pct=Decimal("25"),
                is_on_promotion=True,
            ),
            PriceHistory(
                product_id=p2.id,
                collection_run_id=run.id,
                observed_at=t1,
                price_amount=Decimal("1500"),
                currency="USD",
                is_on_promotion=False,
            ),
        ]
    )
    session.commit()


def test_dashboard_config_loads():
    cfg = load_dashboard_config()
    assert "dashboard" in cfg
    assert "alerts" in cfg
    thr = alert_thresholds()
    assert "compliance_score_below" in thr


def test_filter_propagation_scopes():
    filters = DashboardFilters(
        retailer_code="newegg",
        country_code="US",
        product_type="notebook",
        brand="Intel",
        oem="ASUS",
        date_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
        date_to=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    pricing = to_pricing_scope(filters)
    assert pricing.retailer_code == "newegg"
    assert pricing.brand == "Intel"
    sos = to_sos_scope(filters)
    assert sos.oem == "ASUS"
    sov = to_sov_scope(filters)
    assert sov.country_code == "US"
    cleared = clear_filters()
    assert cleared.retailer_code is None
    prev = previous_period(filters)
    assert prev is not None
    assert prev.date_to == filters.date_from


def test_metric_semantics_no_data_vs_zero():
    empty = MetricValue.from_number(None)
    assert empty.state == DataState.NO_DATA
    assert "No data" in empty.display
    zero = MetricValue.from_number(0, display="0%")
    assert zero.state == DataState.ZERO
    partial = MetricValue.partial("PARTIAL", detail="incomplete")
    assert partial.state == DataState.PARTIAL
    unknown = MetricValue.unknown("PDP blocked")
    assert unknown.state == DataState.UNKNOWN
    blocked = MetricValue.blocked("account verification")
    assert blocked.state == DataState.BLOCKED


def test_fmt_change_insufficient():
    delta, label = fmt_change(None, 1)
    assert delta is None
    assert label == "Insufficient data"
    assert fmt_pct(None) == "No data"
    assert fmt_pct(0) == "0.0%"


def test_tracked_products_and_price_change(session: Session):
    _seed(session)
    filters = DashboardFilters(retailer_code="newegg")
    assert count_tracked_products(session, retailer_code="newegg") == 2
    m = metric_tracked_products(session, filters)
    assert m.value == 2
    assert m.state == DataState.OK
    changed = _count_price_changes(session, filters)
    assert changed == 1  # SKU-1 changed 1000 -> 900; SKU-2 only one obs


def test_average_price_from_db(session: Session):
    _seed(session)
    filters = DashboardFilters(retailer_code="newegg")
    m = metric_average_price(session, filters)
    assert m.state == DataState.OK
    assert m.value is not None
    # latest: 900 and 1500 => avg 1200
    assert abs(float(m.value) - 1200.0) < 0.01


def test_collection_status_partial(session: Session):
    _seed(session)
    snap = load_collection_status(session)
    assert snap.is_partial is True
    assert snap.freshness_label == "PARTIAL DATA"
    assert any(c.status == "PARTIAL" for c in snap.components)
    alerts = build_alerts(
        session,
        DashboardFilters(),
        collection=snap,
        compliance_score=None,
        sos_snap=None,
        sov_snap=None,
    )
    assert any(a.title == "PARTIAL DATA" for a in alerts)
    assert any("mercadolibre" in a.title.lower() for a in alerts)


def test_share_of_shelf_no_fake_when_empty(session: Session):
    # Empty DB — SoS must be NO DATA, not fabricated %
    filters = DashboardFilters()
    m, snap = metric_share_of_shelf(session, filters)
    assert snap.universe_size == 0
    assert m.state == DataState.NO_DATA
    assert m.value is None


def test_retailer_country_brand_oem_product_type_filters(session: Session):
    _seed(session)
    f = DashboardFilters(brand="Intel", product_type="notebook", oem="ASUS", country_code="US")
    m = metric_tracked_products(session, f)
    assert m.value == 1
    f2 = DashboardFilters(brand="Qualcomm")
    m2 = metric_tracked_products(session, f2)
    assert m2.value == 0
    assert m2.state == DataState.ZERO


def test_default_filters_have_date_range():
    f = default_filters()
    assert f.date_from is not None and f.date_to is not None


def test_dashboard_vertical_layout_excludes_product_identity():
    from pathlib import Path

    app_src = Path("dashboard/app.py").read_text(encoding="utf-8")
    assert "identity" not in app_src
    assert "Product Identity" not in app_src
    assert "banners.render" in app_src
    assert "sku_explorer.render" in app_src
    assert "overview.render" in app_src
    assert "collection_status.render" in app_src
    assert "reports.render" in app_src
    order = [line.strip() for line in app_src.splitlines() if ".render(" in line]
    assert order == [
        "collection_status.render(collection)",
        "overview.render(session, filters, collection, refreshed_at)",
        "share_of_shelf.render(session, filters, collection, refreshed_at)",
        "visibility.render(session, filters)",
        "pricing.render(session, filters, collection, refreshed_at)",
        "compliance.render(session, filters, collection, refreshed_at)",
        "banners.render(session, filters, collection, refreshed_at)",
        "attributes.render(session, filters, collection, refreshed_at)",
        "badges.render(session, filters, collection, refreshed_at)",
        "sku_explorer.render(session, filters, collection, refreshed_at)",
        "reports.render()",
    ]
    sos = Path("dashboard/views/share_of_shelf.py").read_text(encoding="utf-8")
    assert "Shelf Presence" in sos
    assert "Share of Shelf" not in sos.split("section_header", 1)[-1][:200]
    sku = Path("dashboard/views/sku_explorer.py").read_text(encoding="utf-8")
    assert "Product Explorer" in sku
    assert "SKU Explorer" not in sku


def test_dynamic_takeaways_use_existing_analytics_not_hardcoded_brands():
    from types import SimpleNamespace
    from decimal import Decimal

    from dashboard.insights_text import (
        NO_DATA,
        attribute_quality_insight,
        compliance_gap_lines,
        compliance_takeaway,
        search_chart_insight,
        search_takeaway,
        shelf_chart_insight,
        shelf_takeaway,
    )

    empty_shelf = shelf_takeaway([])
    assert empty_shelf[1] == NO_DATA
    shares = [
        SimpleNamespace(value="Intel", share=Decimal("0.408")),
        SimpleNamespace(value="AMD", share=Decimal("0.262")),
    ]
    title, detail = shelf_takeaway(shares)
    assert title == "Intel leads shelf presence"
    assert "40.8%" in detail
    insight = shelf_chart_insight(shares)
    assert insight.startswith("Intel leads shelf presence at 40.8%")
    assert "AMD" in insight and "26.2%" in insight

    amd_shares = [
        SimpleNamespace(value="AMD", share=0.51),
        SimpleNamespace(value="Intel", share=0.30),
    ]
    assert shelf_takeaway(amd_shares)[0] == "AMD leads shelf presence"

    metrics = [
        SimpleNamespace(brand="AMD", share_of_voice=Decimal("0.524"), appearances=52),
        SimpleNamespace(brand="Intel", share_of_voice=Decimal("0.476"), appearances=48),
    ]
    stitle, sdetail = search_takeaway(metrics)
    assert stitle == "AMD leads search visibility"
    assert "52.4%" in sdetail
    assert "slightly ahead of Intel" in search_chart_insight(metrics)

    assert compliance_gap_lines([]) == ["No scored checks"]
    assert compliance_gap_lines([("S1", 1.0), ("P3", 1.0)]) == ["No significant compliance gaps"]
    assert compliance_gap_lines([("P3", 0.93), ("S1", 1.0)])[0].startswith("P3")

    weakest = {
        "Intel": [("P3", 0.93), ("S1", 1.0)],
        "AMD": [("S1", 1.0)],
        "Qualcomm": [],
        "Apple": [],
    }
    ctitle, cdetail = compliance_takeaway(weakest)
    assert ctitle == "Intel's main compliance gap"
    assert "P3" in cdetail and "93%" in cdetail

    coverage = [
        {"attribute": "Brand", "coverage_pct": 100.0},
        {"attribute": "Price", "coverage_pct": 100.0},
        {"attribute": "Storage", "coverage_pct": 72.0},
    ]
    attr = attribute_quality_insight(coverage)
    assert "storage has the lowest coverage" in attr.lower()
    assert attribute_quality_insight([]) == NO_DATA


def test_takeaway_cards_render_html_not_indented_markdown():
    from unittest.mock import patch

    from dashboard.components.layout import takeaway_cards

    with patch("dashboard.components.layout.st.markdown") as markdown:
        takeaway_cards(
            [
                ("Intel leads shelf presence", "41.0% of eligible gaming products"),
                ("AMD leads search visibility", "52.4% of tracked-brand appearances"),
            ]
        )
    markdown.assert_called_once()
    html_content, kwargs = markdown.call_args[0][0], markdown.call_args.kwargs
    assert kwargs.get("unsafe_allow_html") is True
    assert html_content.startswith('<div class="ci-takeaways">')
    assert "\n            <div" not in html_content
    assert '<div class="ci-takeaway">' in html_content
    assert "Intel leads shelf presence" in html_content
    assert "AMD leads search visibility" in html_content


def test_extract_product_specs_and_badge_states():
    from dashboard.presentation import badge_coverage_state
    from dashboard.queries.catalog import extract_product_specs

    assert badge_coverage_state(None) == "N/A"
    assert badge_coverage_state(0.86) == "GOOD"
    assert badge_coverage_state(0.67) == "PARTIAL"
    assert badge_coverage_state(0.4) == "LOW"
    specs = extract_product_specs(
        {"processor": "Core Ultra 7", "specs": {"RAM": "32GB", "gpu": "RTX 4070"}}
    )
    assert specs["processor"] == "Core Ultra 7"
    assert specs["gpu"] == "RTX 4070"
    assert specs["ram"] == "32GB"
    empty = extract_product_specs(None)
    assert empty["processor"] is None


def test_search_coverage_display_does_not_falsify_partial_or_missing():
    headline, status, _ = format_search_coverage(observed=None, budget=100, basis="empty")
    assert status == "UNAVAILABLE"
    assert "Not available" in headline

    headline, status, _ = format_search_coverage(observed=100, budget=100, basis="exact")
    assert status == "COMPLETE"
    assert "100 / 100" in headline

    headline, status, _ = format_search_coverage(
        observed=100, budget=100, basis="observed_partial"
    )
    assert status == "PARTIAL"
    assert "observed" in headline.lower()
    assert "/" not in headline

    headline, status, _ = format_search_coverage(observed=0, budget=100, basis="exact")
    assert status == "COMPLETE"
    assert headline.startswith("0 /")

    assert ranked_visibility_available("empty", has_metrics=True) is False
    assert ranked_visibility_available("observed_partial", has_metrics=False) is False
    assert ranked_visibility_available("observed_partial", has_metrics=True) is True
    assert ranked_visibility_available("exact", has_metrics=True) is True


def test_pricing_scope_keeps_currency_separate():
    filters = DashboardFilters(currency="USD")
    pricing = to_pricing_scope(filters)
    assert pricing.currency == "USD"
    mixed = to_pricing_scope(DashboardFilters())
    assert mixed.currency is None


def test_sov_and_visibility_scopes_pass_stratum():
    filters = DashboardFilters(stratum="notebook", retailer_code="newegg")
    assert to_sov_scope(filters).stratum == "notebook"
    from dashboard.filters import to_visibility_scope

    vis = to_visibility_scope(filters)
    assert vis.stratum == "notebook"
    assert vis.top_n == 10


def test_compliance_segment_status_does_not_treat_unknown_as_fail():
    from dashboard.presentation import check_visual_status, format_check_cell

    assert check_visual_status(0, 0, 0) == "UNKNOWN"
    assert check_visual_status(0, 0, 4) == "UNKNOWN"
    assert check_visual_status(13, 1, 0) == "FAIL"
    assert check_visual_status(12, 0, 2) == "PASS"
    assert format_check_cell(0, 0, 0) == "—"
    assert format_check_cell(13, 1, 0) == "93% (14/14)"


def test_compliance_center_uses_existing_segment_score_when_overall_is_null():
    from analytics.compliance.config import CHECK_CODES, ComplianceScoreConfig
    from analytics.compliance.models import AuditScoreRow
    from analytics.compliance.scoring import compute_brand_scores
    from dashboard.components.charts import _compliance_ring_figure
    from dashboard.filters import DashboardFilters, audit_row_matches, default_filters
    from dashboard.presentation import (
        TRACKED_PLATFORM_BRANDS,
        brand_score_lookup,
        displayed_compliance_center,
    )

    cfg = ComplianceScoreConfig()
    intel_rows = [
        AuditScoreRow(
            brand="Intel",
            retailer_code="newegg",
            country_code="US",
            product_type="notebook",
            check_code=code,
            result="PASS" if code != "P3" else "FAIL",
            product_id=1,
        )
        for code in CHECK_CODES
    ]
    amd_rows = [
        AuditScoreRow(
            brand="AMD",
            retailer_code="newegg",
            country_code="US",
            product_type="notebook",
            check_code=code,
            result="PASS",
            product_id=2,
        )
        for code in CHECK_CODES
    ]
    unknown_only = [
        AuditScoreRow(
            brand="Intel",
            retailer_code="newegg",
            country_code="US",
            product_type="notebook",
            check_code="S1",
            result="UNKNOWN",
            product_id=3,
        )
    ]
    scores = compute_brand_scores(intel_rows + amd_rows, config=cfg)
    intel = brand_score_lookup(scores, "Intel")
    amd = brand_score_lookup(scores, "AMD")
    assert intel.overall_score is None
    assert amd.overall_score is None
    intel_center, intel_sub = displayed_compliance_center(intel)
    amd_center, amd_sub = displayed_compliance_center(amd)
    assert intel_center == intel.notebook.score
    assert intel_center is not None
    assert intel_sub == "Pass Rate"
    assert amd_center == pytest.approx(1.0)
    assert amd_sub == "Pass Rate"
    assert displayed_compliance_center(None) == (None, "No Data")
    empty_q = displayed_compliance_center(brand_score_lookup(scores, "Qualcomm"))
    empty_a = displayed_compliance_center(brand_score_lookup(scores, "Apple"))
    assert empty_q == (None, "No Data")
    assert empty_a == (None, "No Data")

    unknown_scores = compute_brand_scores(unknown_only, config=cfg)
    unknown_center, unknown_sub = displayed_compliance_center(unknown_scores["Intel"])
    assert unknown_center is None
    assert unknown_sub == "No Data"

    dated = default_filters()
    assert dated.date_from is not None
    for row in intel_rows:
        assert audit_row_matches(row, dated) is True
        assert audit_row_matches(row, DashboardFilters(date_from=dated.date_from, date_to=dated.date_to)) is True

    rings = []
    for brand in TRACKED_PLATFORM_BRANDS:
        sc = brand_score_lookup(scores, brand)
        center, subtitle = displayed_compliance_center(sc)
        rings.append(
            {
                "brand": brand,
                "overall": center,
                "center_subtitle": subtitle,
                "checks": [{"code": code, "status": "UNKNOWN", "pass": 0, "fail": 0, "unknown": 0} for code in CHECK_CODES],
            }
        )
    assert len(rings) == 4
    for ring in rings:
        fig = _compliance_ring_figure(ring)
        labels = list(fig.data[0].labels)
        assert labels == list(CHECK_CODES)
        assert len(fig.data[0].values) == 7
        assert list(fig.data[0].values) == [1] * 7
        assert fig.data[0].type == "pie"
        assert 0.4 < float(fig.data[0].hole) < 0.8
        assert fig.layout.height <= 260
        pie = fig.data[0]
        domain = pie.domain
        assert domain.x[1] - domain.x[0] <= 1
        assert domain.y[1] - domain.y[0] <= 1
        assert abs((domain.x[1] - domain.x[0]) - (domain.y[1] - domain.y[0])) < 0.01


def test_brand_compliance_section_presentation_uses_existing_scores():
    from analytics.compliance.config import CHECK_CODES
    from analytics.compliance.models import AuditScoreRow
    from analytics.compliance.scoring import compute_brand_scores, compute_compliance_score
    from dashboard.components.charts import _compliance_ring_figure
    from dashboard.presentation import (
        CHECK_LABELS,
        TRACKED_PLATFORM_BRANDS,
        format_center_percent,
        format_check_status_cell,
        lowest_scored_checks,
        status_color,
    )
    from dashboard.views.compliance import build_compliance_presentation

    intel_rows = [
        AuditScoreRow(
            brand="Intel",
            retailer_code="newegg",
            country_code="US",
            product_type="notebook",
            check_code=code,
            result="PASS" if code != "P2" else "FAIL",
            product_id=1,
        )
        for code in CHECK_CODES
    ]
    amd_rows = [
        AuditScoreRow(
            brand="AMD",
            retailer_code="newegg",
            country_code="US",
            product_type="notebook",
            check_code=code,
            result="PASS" if code not in {"P2", "S2"} else "FAIL",
            product_id=2,
        )
        for code in CHECK_CODES
    ]
    rows = intel_rows + amd_rows
    overall = compute_compliance_score(rows)
    scores = compute_brand_scores(rows)
    model = build_compliance_presentation(overall, scores)

    assert [b.brand for b in model.brands] == list(TRACKED_PLATFORM_BRANDS)
    assert list(CHECK_CODES) == ["S1", "S2", "P1", "P2", "P3", "P4", "P5"]
    assert all(code in CHECK_LABELS for code in CHECK_CODES)

    intel = next(b for b in model.brands if b.brand == "Intel")
    amd = next(b for b in model.brands if b.brand == "AMD")
    qualcomm = next(b for b in model.brands if b.brand == "Qualcomm")
    apple = next(b for b in model.brands if b.brand == "Apple")

    assert intel.has_scored_checks is True
    assert amd.has_scored_checks is True
    assert qualcomm.has_scored_checks is False
    assert apple.has_scored_checks is False
    assert format_center_percent(qualcomm.center_score) == "N/A"
    assert format_center_percent(apple.center_score) == "N/A"
    assert qualcomm.coverage_label == "—"
    assert "0%" not in format_center_percent(qualcomm.center_score)
    for row in qualcomm.check_rows:
        assert row["cell"] == "N/A —"
        assert "0%" not in row["cell"]

    assert intel.lowest == lowest_scored_checks(intel.ranked, limit=3)
    assert intel.lowest[0][0] == "P2"
    assert intel.lowest[0][1] == intel.ranked[0][1]
    assert intel.lowest == sorted(intel.lowest, key=lambda item: item[1])
    assert amd.lowest[0][1] <= amd.lowest[-1][1]
    assert set(code for code, _ in intel.ranked) <= set(CHECK_CODES)

    p2_intel = next(r for r in intel.check_rows if r["code"] == "P2")
    s1_intel = next(r for r in intel.check_rows if r["code"] == "S1")
    assert p2_intel["status"] == "FAIL"
    assert "✕" in p2_intel["cell"]
    assert s1_intel["status"] == "PASS"
    assert "✓" in s1_intel["cell"]
    assert status_color("PASS") != status_color("FAIL") != status_color("UNKNOWN")

    na_text, na_status = format_check_status_cell(0, 0, 5)
    assert na_text == "N/A —"
    assert na_status == "UNKNOWN"
    assert "0%" not in na_text
    zero_fail_text, zero_fail_status = format_check_status_cell(0, 4, 0)
    assert zero_fail_status == "FAIL"
    assert zero_fail_text.startswith("0%")

    for brand in model.brands:
        fig = _compliance_ring_figure(brand.ring)
        pie = fig.data[0]
        assert list(pie.labels) == list(CHECK_CODES)
        assert list(pie.values) == [1] * 7
        assert pie.type == "pie"
        assert pie.hole >= 0.5
        domain = pie.domain
        assert abs((domain.x[1] - domain.x[0]) - (domain.y[1] - domain.y[0])) < 0.01
        annotation = fig.layout.annotations[0].text
        if brand.center_score is None:
            assert "N/A" in annotation
            assert "0%" not in annotation
        else:
            assert "%" in annotation

    assert overall.coverage.pass_count == model.pass_count
    assert overall.coverage.fail_count == model.fail_count
    assert overall.coverage.unknown_count == model.unknown_count
    recomputed = compute_compliance_score(rows)
    assert recomputed.overall_score == overall.overall_score
    assert recomputed.coverage.pass_count == overall.coverage.pass_count


def test_db_connection_probe_optional():
    from dashboard.db import check_connection

    ok, msg = check_connection()
    assert isinstance(ok, bool)
    assert isinstance(msg, str)
    # If Postgres is unavailable in CI, ok may be False — that is acceptable.
