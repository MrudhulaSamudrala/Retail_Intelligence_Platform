"""Current compliance universe: latest eligible audits (no live sites, no collection)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.compliance import compute_compliance_score
from analytics.compliance.queries import (
    NEWEGG_STRATIFIED_AUDIT_LIMITATION,
    evaluate_platform_badges,
    load_audit_rows,
)
from collector.parsers.badges import BadgeEvidence
from database.models import Base, Product, RetailerAudit, SearchObservation
from database.repositories import (
    CollectionRunRepository,
    ObservationRepository,
    ProductRepository,
)


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, connection_record):  # type: ignore[untyped-def]
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


def _run(
    session: Session, *, retailer: str = "mercadolibre", country: str = "BR"
) -> int:
    row = CollectionRunRepository(session).start(
        retailer_code=retailer, country_code=country, run_type="catalog"
    )
    session.flush()
    return int(row.id)


def _product(
    session: Session,
    *,
    run_id: int,
    sku: str,
    title: str,
    brand: str,
    product_type: str,
    oem: str = "Asus",
    retailer: str = "mercadolibre",
    country: str = "BR",
) -> Product:
    product = ProductRepository(session).upsert_identity(
        retailer_code=retailer,
        country_code=country,
        retailer_sku=sku,
        canonical_url=f"https://example.test/{sku}",
        title=title,
        brand=brand,
        oem=oem,
        product_type=product_type,
        collection_run_id=run_id,
    )
    session.flush()
    return product


def _audit(
    session: Session,
    *,
    product: Product,
    run_id: int,
    check_code: str,
    result: str,
    observed_at: datetime,
    brand: str | None = None,
    product_type: str | None = None,
    details: dict | None = None,
) -> RetailerAudit:
    row = ObservationRepository(session).add_audit(
        product_id=product.id,
        collection_run_id=run_id,
        observed_at=observed_at,
        retailer_code=product.retailer_code,
        country_code=product.country_code,
        brand=brand if brand is not None else product.brand,
        product_type=product_type if product_type is not None else product.product_type,
        check_code=check_code,
        result=result,
        details=details,
    )
    session.flush()
    return row


def test_latest_audit_per_product_check_is_selected(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="nb-1",
        title="ASUS TUF Gaming Intel Core Ultra 7 laptop",
        brand="Intel",
        product_type="notebook",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="FAIL",
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="PASS",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    rows = load_audit_rows(session)
    assert len(rows) == 1
    assert rows[0].result == "PASS"
    assert rows[0].check_code == "S1"
    assert rows[0].product_id == product.id


def test_historical_pass_does_not_override_newer_fail(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="nb-2",
        title="Lenovo Legion Intel Core i7 laptop",
        brand="Intel",
        product_type="notebook",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="PASS",
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="FAIL",
        observed_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    rows = load_audit_rows(session)
    assert [r.result for r in rows] == ["FAIL"]


def test_historical_fail_does_not_override_newer_pass(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="nb-3",
        title="HP Victus Intel Core i5 laptop",
        brand="Intel",
        product_type="notebook",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="P1",
        result="FAIL",
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="P1",
        result="PASS",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    rows = load_audit_rows(session)
    assert [r.result for r in rows] == ["PASS"]


def test_excluded_product_is_not_scored(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="desk-1",
        title='Electric RGB Gaming Standing Desk 55"',
        brand="Intel",
        product_type="notebook",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="PASS",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert load_audit_rows(session) == []


def test_product_type_other_is_not_scored(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="other-1",
        title="Generic accessory bundle",
        brand="Intel",
        product_type="other",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="FAIL",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert load_audit_rows(session) == []


def test_furniture_is_not_scored(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="furn-1",
        title='Electric RGB Gaming Standing Desk 55"',
        brand="AMD",
        product_type="workstation",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="PASS",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert load_audit_rows(session) == []


def test_unknown_brand_does_not_receive_invented_tracked_requirement(
    session: Session,
) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="unk-1",
        title="Gaming notebook 16GB RAM",
        brand="UNKNOWN",
        product_type="notebook",
        oem="Unknown OEM",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="FAIL",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        brand="UNKNOWN",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S2",
        result="UNKNOWN",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        brand="UNKNOWN",
        details={"reason": "brand_unknown_cannot_audit"},
    )
    rows = load_audit_rows(session)
    assert all(r.result != "FAIL" for r in rows)
    assert all(r.check_code != "S1" or r.result == "UNKNOWN" for r in rows)
    assert any(r.check_code == "S2" and r.result == "UNKNOWN" for r in rows)


def test_other_brand_does_not_receive_tracked_brand_requirements(
    session: Session,
) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="nv-1",
        title="NVIDIA GeForce RTX 4070 graphics card",
        brand="OTHER",
        product_type="gpu",
        oem="NVIDIA",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="FAIL",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        brand="OTHER",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="P5",
        result="PASS",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        brand="OTHER",
    )
    rows = load_audit_rows(session)
    assert all(r.check_code != "S1" for r in rows)
    assert any(r.check_code == "P5" and r.result == "PASS" for r in rows)


def test_other_is_not_counted_as_fail(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="nv-2",
        title="NVIDIA GeForce RTX 4080 graphics card",
        brand="OTHER",
        product_type="gpu",
        oem="NVIDIA",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="P1",
        result="FAIL",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        brand="OTHER",
    )
    score = compute_compliance_score(load_audit_rows(session))
    assert score.coverage.fail_count == 0
    assert score.coverage.pass_count == 0


def test_pdp_blocked_is_unknown(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="nb-block",
        title="Dell G15 Intel Core i7 laptop",
        brand="Intel",
        product_type="notebook",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="P4",
        result="UNKNOWN",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        details={"reason": "PDP_BLOCKED"},
    )
    rows = load_audit_rows(session)
    assert len(rows) == 1
    assert rows[0].result == "UNKNOWN"


def test_missing_evidence_is_unknown(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="nb-miss",
        title="Acer Nitro Intel Core i7 laptop",
        brand="Intel",
        product_type="notebook",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="UNKNOWN",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        details={"reason": "listing_badge_evidence_missing"},
    )
    rows = load_audit_rows(session)
    assert [r.result for r in rows] == ["UNKNOWN"]
    score = compute_compliance_score(rows)
    assert score.coverage.fail_count == 0
    assert score.coverage.pass_count == 0
    assert score.coverage.unknown_count == 1


def test_unknown_excluded_from_pass_fail_denominator(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="nb-den",
        title="MSI Thin Intel Core i7 laptop",
        brand="Intel",
        product_type="notebook",
        oem="MSI",
    )
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="PASS",
        observed_at=now,
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S2",
        result="FAIL",
        observed_at=now,
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="P1",
        result="UNKNOWN",
        observed_at=now,
    )
    score = compute_compliance_score(load_audit_rows(session))
    assert score.coverage.pass_count == 1
    assert score.coverage.fail_count == 1
    assert score.coverage.unknown_count == 1
    assert score.coverage.pass_rate == pytest.approx(0.5)
    assert score.notebook is not None
    assert score.notebook.coverage.pass_rate == pytest.approx(0.5)


def test_pass_calculation_and_weighting_unchanged(session: Session) -> None:
    run_id = _run(session)
    notebook = _product(
        session,
        run_id=run_id,
        sku="nb-w",
        title="ASUS TUF Intel Core Ultra laptop",
        brand="Intel",
        product_type="notebook",
    )
    desktop = _product(
        session,
        run_id=run_id,
        sku="dt-w",
        title="ASUS ROG Intel Core i7 desktop",
        brand="Intel",
        product_type="desktop",
    )
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    _audit(
        session,
        product=notebook,
        run_id=run_id,
        check_code="S1",
        result="PASS",
        observed_at=now,
    )
    _audit(
        session,
        product=desktop,
        run_id=run_id,
        check_code="S1",
        result="FAIL",
        observed_at=now,
    )
    score = compute_compliance_score(load_audit_rows(session))
    assert score.notebook_weight == pytest.approx(0.85)
    assert score.desktop_weight == pytest.approx(0.15)
    assert score.notebook is not None and score.notebook.score == pytest.approx(1.0)
    assert score.desktop is not None and score.desktop.score == pytest.approx(0.0)
    assert score.overall_score == pytest.approx(0.85 * 1.0 + 0.15 * 0.0)


def test_apple_brand_and_oem_is_one_product_identity(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="mac-1",
        title="Apple MacBook Pro 14 M3 Pro",
        brand="Apple",
        product_type="notebook",
        oem="Apple",
    )
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="P4",
        result="PASS",
        observed_at=now,
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="P5",
        result="PASS",
        observed_at=now,
    )
    rows = load_audit_rows(session)
    assert {r.check_code for r in rows} == {"P4", "P5"}
    assert {r.product_id for r in rows} == {product.id}
    assert len({r.product_id for r in rows}) == 1


@pytest.mark.parametrize(
    "family,processor,title,evidence",
    [
        (
            "intel_core",
            "Intel Core i7-13700H",
            "Gaming laptop",
            BadgeEvidence(badge_texts=["Intel Core"]),
        ),
        (
            "intel_core_ultra",
            "Intel Core Ultra 7 255H",
            "Ultrabook",
            BadgeEvidence(badge_texts=["Intel® Core™ Ultra"]),
        ),
        (
            "intel_evo",
            "Intel Core Ultra 5 125H",
            "Intel Evo Edition laptop",
            BadgeEvidence(badge_texts=["Intel Evo badge"]),
        ),
        (
            "intel_vpro",
            "Intel Core i7 vPro",
            "Business laptop",
            BadgeEvidence(badge_texts=["Intel® vPro® badge"]),
        ),
        (
            "amd_ryzen",
            "AMD Ryzen 7 8845HS",
            "TUF Gaming",
            BadgeEvidence(badge_texts=["AMD Ryzen"]),
        ),
        (
            "amd_ryzen_ai",
            "AMD Ryzen AI 9 HX 370",
            "AI laptop",
            BadgeEvidence(img_alts=["AMD Ryzen AI"]),
        ),
        (
            "qualcomm_snapdragon",
            "Qualcomm Snapdragon X Elite",
            "Copilot+ PC",
            BadgeEvidence(element_texts=["Qualcomm Snapdragon"]),
        ),
        (
            "apple_silicon",
            "Apple M2",
            "Apple MacBook Air",
            BadgeEvidence(badge_texts=["Apple Silicon", "Apple M2"]),
        ),
        (
            "apple_m_series",
            "Apple M4 Pro",
            "MacBook Pro",
            BadgeEvidence(img_alts=["Apple M4 Pro"]),
        ),
    ],
)
def test_existing_badge_detector_families(
    family: str, processor: str, title: str, evidence: BadgeEvidence
) -> None:
    view = evaluate_platform_badges(
        processor=processor,
        title=title,
        evidence=evidence,
    )
    assert family in view.expected
    assert family in view.detected
    assert family in view.correct
    assert family not in view.missing
    assert view.inspected is True


def test_badge_missing_only_when_inspectable_and_expected() -> None:
    inspected_missing = evaluate_platform_badges(
        processor="Intel Core Ultra 7 255H",
        title="Gaming notebook",
        evidence=BadgeEvidence(badge_texts=["unrelated promo sticker"], page_text=" "),
    )
    assert "intel_core_ultra" in inspected_missing.expected
    assert "intel_evo" not in inspected_missing.expected
    assert "intel_evo" not in inspected_missing.missing
    assert inspected_missing.inspected is True
    assert "intel_core_ultra" in inspected_missing.missing

    not_inspectable = evaluate_platform_badges(
        processor="Intel Core Ultra 7 255H",
        title="Gaming notebook",
        evidence=None,
    )
    assert not_inspectable.inspected is False
    assert not_inspectable.missing == ()
    assert "intel_core_ultra" in not_inspectable.unknown_families
    assert not_inspectable.status == "unknown"

    brand_only = evaluate_platform_badges(
        brand="Intel",
        title="Generic notebook",
        evidence=BadgeEvidence(badge_texts=["Intel inside-style mark"]),
    )
    assert "intel_evo" not in brand_only.expected
    assert "intel_evo" not in brand_only.missing


def test_no_badge_evidence_is_unknown_not_fail() -> None:
    view = evaluate_platform_badges(
        processor="Qualcomm Snapdragon X Elite",
        title="Snapdragon laptop",
        evidence=BadgeEvidence(),
    )
    assert view.status == "unknown"
    assert view.missing == ()
    assert view.evaluation.missing  # detector may list missing internally
    assert "qualcomm_snapdragon" in view.unknown_families


def test_historical_audit_rows_remain(session: Session) -> None:
    run_id = _run(session)
    product = _product(
        session,
        run_id=run_id,
        sku="nb-hist",
        title="Lenovo LOQ Intel Core i7 laptop",
        brand="Intel",
        product_type="notebook",
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="FAIL",
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    _audit(
        session,
        product=product,
        run_id=run_id,
        check_code="S1",
        result="PASS",
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    load_audit_rows(session)
    stored = session.scalar(select(func.count()).select_from(RetailerAudit))
    assert stored == 2
    historical = load_audit_rows(session, current_universe=False)
    assert {r.result for r in historical} == {"PASS", "FAIL"}


def test_newegg_does_not_receive_fabricated_s1_p5_audits(session: Session) -> None:
    run_id = _run(session, retailer="newegg", country="US")
    product = _product(
        session,
        run_id=run_id,
        sku="N82E168",
        title="ASUS TUF Intel Core Ultra laptop",
        brand="Intel",
        product_type="notebook",
        retailer="newegg",
        country="US",
    )
    ObservationRepository(session).add_search(
        collection_run_id=run_id,
        product_id=product.id,
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        retailer_code="newegg",
        country_code="US",
        keyword="notebook",
        position=1,
        retailer_sku=product.retailer_sku,
        title=product.title,
        brand="Intel",
        stratum="notebook",
        observation_source="stratified_catalog",
        collection_status="COMPLETE",
    )
    session.flush()
    assert NEWEGG_STRATIFIED_AUDIT_LIMITATION == (
        "Newegg stratified collection currently lacks persisted S1–P5 audit rows."
    )
    assert session.scalar(select(func.count()).select_from(RetailerAudit)) == 0
    assert session.scalar(select(func.count()).select_from(SearchObservation)) == 1
    assert load_audit_rows(session) == []
    assert session.scalar(select(func.count()).select_from(RetailerAudit)) == 0
