"""Newegg S1–P5 evidence persistence through the existing compliance engine."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.compliance.queries import load_audit_rows
from collector.audit.checks import evaluate_all_checks
from collector.audit.models import AuditContext, ListingEvidence, ProductEvidence
from collector.normalize import build_normalized_product
from collector.parsers.badges import BadgeEvidence, evaluate_badges
from collector.persist import CollectionPersister
from collector.pipeline import CollectionPipeline, _compliance_persist_eligible
from collector.retailers.newegg.collector import NeweggCollector
from collector.retailers.newegg.product_page import build_from_listing
from database.models import Base, Badge, RetailerAudit
from database.repositories import ObservationRepository


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


def _pipe(session: Session) -> CollectionPipeline:
    return CollectionPipeline(session, NeweggCollector())


def _persist(
    session: Session, product, *, run_id: int, observed_at: datetime | None = None
) -> int:
    observed = observed_at or datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    pipe = _pipe(session)
    product_id = pipe.persister.save_product(
        product, collection_run_id=run_id, observed_at=observed
    )
    pipe._persist_surface_evidence(
        product,
        product_id=product_id,
        collection_run_id=run_id,
        observed_at=observed,
    )
    session.flush()
    return product_id


def _notebook(**raw_extra) -> object:
    raw = {
        "listing_audit": {
            "title": "ASUS TUF Gaming Intel Core Ultra 7 laptop",
            "tile_text": "ASUS TUF Gaming Intel Core Ultra 7",
            "badge_texts": [],
            "selectors_used": ["listing_card"],
            "source_url": "https://www.newegg.com/p/N82E16834200001",
            "available": True,
        },
        "pdp_audit": {
            "badges_inspected": True,
            "media_inspected": True,
            "badge_texts": ["Intel Core Ultra"],
            "brand_media_signals": ["Intel Core Ultra"],
            "oem_media_signals": ["ASUS TUF"],
            "selectors_used": ["h1.product-title"],
            "specs_available": True,
        },
        "badge_signals": {
            "badge_texts": ["Intel Core Ultra"],
            "img_alts": ["Intel Core Ultra"],
            "img_titles": [],
            "aria_labels": [],
        },
        "detail_page_status": "ok",
        "specs": {"Processor": "Intel Core Ultra 7 255H"},
    }
    raw.update(raw_extra)
    return build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="N82E16834200001",
        source_url="https://www.newegg.com/p/N82E16834200001",
        title="ASUS TUF Gaming Intel Core Ultra 7 laptop",
        category_raw="notebook",
        processor="Intel Core Ultra 7 255H",
        specs={"Processor": "Intel Core Ultra 7 255H"},
        raw_payload=raw,
    )


def test_newegg_notebook_produces_audit_rows(session: Session) -> None:
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    product_id = _persist(session, _notebook(), run_id=int(run.id))
    rows = session.scalars(select(RetailerAudit)).all()
    assert {r.check_code for r in rows} == {"S1", "S2", "P1", "P2", "P3", "P4", "P5"}
    assert all(r.product_id == product_id for r in rows)
    assert all(r.retailer_code == "newegg" for r in rows)
    assert all(r.collection_run_id == run.id for r in rows)
    by_code = {r.check_code: r.result for r in rows}
    assert by_code["S1"] == "PASS"
    assert by_code["P1"] == "PASS"
    assert by_code["P3"] == "PASS"


def test_newegg_desktop_produces_audit_rows(session: Session) -> None:
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    product = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="N82E16833100001",
        source_url="https://www.newegg.com/p/N82E16833100001",
        title="ASUS ROG Intel Core i7 gaming desktop",
        category_raw="desktop",
        processor="Intel Core i7-14700K",
        specs={"Processor": "Intel Core i7-14700K"},
        raw_payload={
            "listing_audit": {
                "title": "ASUS ROG Intel Core i7 gaming desktop",
                "available": True,
                "source_url": "https://www.newegg.com/p/N82E16833100001",
            },
            "pdp_audit": {"badges_inspected": False, "media_inspected": False},
            "detail_page_status": "ok",
        },
    )
    assert product.product_type == "desktop"
    _persist(session, product, run_id=int(run.id))
    rows = session.scalars(select(RetailerAudit)).all()
    assert len(rows) == 7
    assert all(r.product_type == "desktop" for r in rows)


@pytest.mark.parametrize(
    "title,sku,expected_type",
    [
        ("Lenovo ThinkStation Intel Xeon gaming workstation", "WS1", "workstation"),
        ("Samsung Galaxy Tab Snapdragon gaming tablet", "TB1", "tablet"),
        ("AMD Ryzen 7 7800X3D Processor", "CPU1", "cpu"),
        ("GIGABYTE NVIDIA GeForce RTX 4070 graphics card", "GPU1", "gpu"),
    ],
)
def test_newegg_supported_types_follow_shared_classifier(
    session: Session, title: str, sku: str, expected_type: str
) -> None:
    product = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku=sku,
        source_url=f"https://www.newegg.com/p/{sku}",
        title=title,
        specs={},
        raw_payload={
            "listing_audit": {"title": title, "available": True},
            "pdp_audit": {"badges_inspected": False, "media_inspected": False},
        },
    )
    assert product.product_type == expected_type
    assert _compliance_persist_eligible(product) is True
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _persist(session, product, run_id=int(run.id))
    assert session.scalar(select(func.count()).select_from(RetailerAudit)) == 7


def test_excluded_product_produces_no_compliance_audit(session: Session) -> None:
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    product = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="DESK1",
        source_url="https://www.newegg.com/p/DESK1",
        title='Electric RGB Gaming Standing Desk 55"',
        category_raw="workstation",
        raw_payload={"listing_audit": {"title": "desk", "available": True}},
    )
    assert _compliance_persist_eligible(product) is False
    _persist(session, product, run_id=int(run.id))
    assert session.scalar(select(func.count()).select_from(RetailerAudit)) == 0


def test_unknown_brand_does_not_invent_tracked_requirement(session: Session) -> None:
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    product = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="UNK1",
        source_url="https://www.newegg.com/p/UNK1",
        title="Generic 16-inch gaming notebook 16GB RAM",
        category_raw="notebook",
        raw_payload={
            "listing_audit": {
                "title": "Generic 16-inch gaming notebook 16GB RAM",
                "available": True,
            },
            "pdp_audit": {"badges_inspected": False, "media_inspected": False},
            "detail_page_status": "ok",
        },
    )
    assert product.brand == "UNKNOWN"
    _persist(session, product, run_id=int(run.id))
    rows = session.scalars(select(RetailerAudit)).all()
    tracked = [r for r in rows if r.check_code in {"S1", "S2", "P1", "P2", "P3", "P4"}]
    assert tracked
    assert all(r.result == "UNKNOWN" for r in tracked)
    assert all((r.details or {}).get("reason") == "brand_unknown_cannot_audit" for r in tracked)


def test_other_brand_skips_s1_p4_in_current_analytics(session: Session) -> None:
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    product = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="NV1",
        source_url="https://www.newegg.com/p/NV1",
        title="GIGABYTE NVIDIA GeForce RTX 4070 graphics card",
        category_raw="gpu",
        gpu="GeForce RTX 4070",
        specs={"GPU": "NVIDIA GeForce RTX 4070"},
        raw_payload={
            "listing_audit": {
                "title": "GIGABYTE NVIDIA GeForce RTX 4070 graphics card",
                "available": True,
            },
            "pdp_audit": {"badges_inspected": False, "media_inspected": False},
            "detail_page_status": "ok",
        },
    )
    assert product.brand == "OTHER"
    _persist(session, product, run_id=int(run.id))
    stored = session.scalars(select(RetailerAudit)).all()
    assert stored
    current = load_audit_rows(session)
    assert all(r.check_code not in {"S1", "S2", "P1", "P2", "P3", "P4"} for r in current)
    assert all(r.result != "FAIL" or r.check_code == "P5" for r in current)


def test_pdp_blocked_is_unknown(session: Session) -> None:
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    product = build_from_listing(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        sku="N82BLOCK",
        source_url="https://www.newegg.com/p/N82BLOCK",
        title="MSI Stealth Intel Core Ultra 7 gaming laptop",
        category_raw="notebook",
        detail_page_status="bot_challenge",
        listing_raw={"title": "MSI Stealth Intel Core Ultra 7 gaming laptop"},
    )
    _persist(session, product, run_id=int(run.id))
    rows = {r.check_code: r for r in session.scalars(select(RetailerAudit)).all()}
    assert rows["S1"].result == "PASS"
    assert rows["P2"].result == "UNKNOWN"
    assert rows["P3"].result == "UNKNOWN"
    assert rows["P4"].result == "UNKNOWN"
    assert rows["P5"].result == "UNKNOWN"
    p3_reason = (rows["P3"].details or {}).get("reason") or ""
    assert "PDP_BLOCKED" in p3_reason or p3_reason == "specification_table_unavailable"


def test_missing_evidence_is_unknown(session: Session) -> None:
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    product = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="N82MISS",
        source_url="https://www.newegg.com/p/N82MISS",
        title="ASUS TUF Intel Core i7 gaming laptop",
        processor="Intel Core i7-13700H",
        specs={"Processor": "Intel Core i7-13700H"},
        raw_payload={
            "listing_audit": {
                "title": "ASUS TUF Intel Core i7 gaming laptop",
                "available": True,
            },
            "pdp_audit": {
                "badges_inspected": False,
                "media_inspected": False,
                "badge_texts": [],
            },
            "detail_page_status": "ok",
        },
    )
    _persist(session, product, run_id=int(run.id))
    rows = {r.check_code: r.result for r in session.scalars(select(RetailerAudit)).all()}
    assert rows["S2"] == "UNKNOWN"
    assert rows["P2"] == "UNKNOWN"
    assert rows["P4"] == "UNKNOWN"
    assert rows["P5"] == "UNKNOWN"


def test_observed_failure_is_fail(session: Session) -> None:
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    product = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="N82FAIL",
        source_url="https://www.newegg.com/p/N82FAIL",
        title="MSI Thin 15 16GB DDR5 gaming laptop Intel Core i7",
        specs={"Memory": "16GB"},
        raw_payload={
            "listing_audit": {
                "title": "MSI Thin 15 16GB DDR5 gaming laptop",
                "available": True,
                "source_url": "https://www.newegg.com/p/N82FAIL",
            },
            "pdp_audit": {
                "badges_inspected": True,
                "media_inspected": True,
                "badge_texts": ["Best Seller"],
                "brand_media_signals": ["product photo"],
                "oem_media_signals": ["product photo"],
                "specs_available": True,
            },
            "detail_page_status": "ok",
            "specs": {"Memory": "16GB"},
        },
    )
    assert product.brand == "Intel"
    _persist(session, product, run_id=int(run.id))
    rows = {r.check_code: r.result for r in session.scalars(select(RetailerAudit)).all()}
    assert rows["S1"] == "FAIL"
    assert rows["P3"] == "FAIL"


def test_observed_compliance_is_pass(session: Session) -> None:
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _persist(session, _notebook(), run_id=int(run.id))
    rows = {r.check_code: r.result for r in session.scalars(select(RetailerAudit)).all()}
    assert rows["S1"] == "PASS"
    assert rows["P1"] == "PASS"
    assert rows["P3"] == "PASS"


def test_badge_evaluation_uses_existing_engine() -> None:
    evaluation = evaluate_badges(
        processor="Intel Core Ultra 7 255H",
        title="ASUS TUF Gaming laptop",
        evidence=BadgeEvidence(badge_texts=["Intel® Core™ Ultra"]),
    )
    assert "intel_core_ultra" in evaluation.expected
    assert "intel_core_ultra" in evaluation.correct
    notebook = _notebook()
    persist_eval = evaluate_badges(
        processor=notebook.processor,
        title=notebook.title,
        specifications=(notebook.raw_payload or {}).get("specs"),
        brand=notebook.brand,
        evidence=BadgeEvidence(
            badge_texts=["Intel Core Ultra"], img_alts=["Intel Core Ultra"]
        ),
    )
    assert persist_eval.expected == evaluation.expected


def test_collection_run_id_persisted(session: Session) -> None:
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _persist(session, _notebook(), run_id=int(run.id))
    rows = session.scalars(select(RetailerAudit)).all()
    assert rows
    assert {r.collection_run_id for r in rows} == {run.id}


def test_historical_audits_unchanged(session: Session) -> None:
    persister = CollectionPersister(session)
    old_run = persister.start_run(
        retailer_code="newegg", country_code="US", run_type="audit"
    )
    product_id = persister.save_product(
        _notebook(),
        collection_run_id=int(old_run.id),
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    historical = ObservationRepository(session).add_audit(
        product_id=product_id,
        collection_run_id=int(old_run.id),
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        retailer_code="newegg",
        country_code="US",
        brand="Intel",
        product_type="notebook",
        check_code="S1",
        result="FAIL",
        details={"historical": True},
    )
    session.flush()
    historical_id = historical.id
    new_run = persister.start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _persist(
        session,
        _notebook(),
        run_id=int(new_run.id),
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    kept = session.get(RetailerAudit, historical_id)
    assert kept is not None
    assert kept.result == "FAIL"
    assert kept.details == {"historical": True}
    assert kept.collection_run_id == old_run.id
    assert session.scalar(select(func.count()).select_from(RetailerAudit)) > 1


def test_latest_audit_selection_still_works(session: Session) -> None:
    persister = CollectionPersister(session)
    run = persister.start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    product = _notebook()
    product_id = _persist(
        session,
        product,
        run_id=int(run.id),
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    ObservationRepository(session).add_audit(
        product_id=product_id,
        collection_run_id=int(run.id),
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        retailer_code="newegg",
        country_code="US",
        brand="Intel",
        product_type="notebook",
        check_code="S1",
        result="FAIL",
    )
    session.flush()
    current = [r for r in load_audit_rows(session) if r.check_code == "S1"]
    assert len(current) == 1
    assert current[0].result == "FAIL"


def test_newegg_and_mercadolibre_use_same_s1_p5_engine() -> None:
    listing = ListingEvidence(
        title="ASUS TUF Gaming AMD Ryzen 7 laptop",
        available=True,
        source_url="https://example.test/p/1",
    )
    product = ProductEvidence(
        title="ASUS TUF Gaming AMD Ryzen 7 260",
        specs={"CPU": "AMD Ryzen 7 260"},
        specs_available=True,
        badges_inspected=False,
        media_inspected=False,
        source_url="https://example.test/p/1",
    )
    newegg = evaluate_all_checks(
        AuditContext(
            retailer_code="newegg",
            country_code="US",
            brand="AMD",
            oem="Asus",
            product_type="notebook",
            listing=listing,
            product=product,
        )
    )
    ml = evaluate_all_checks(
        AuditContext(
            retailer_code="mercadolibre",
            country_code="BR",
            brand="AMD",
            oem="Asus",
            product_type="notebook",
            listing=listing,
            product=product,
        )
    )
    assert [(r.check_code, r.result, r.details) for r in newegg] == [
        (r.check_code, r.result, r.details) for r in ml
    ]


def test_listing_only_builder_does_not_invent_pdp_badges() -> None:
    product = build_from_listing(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        sku="N82X",
        source_url="https://www.newegg.com/p/N82X",
        title="ASUS TUF Intel Core i7 gaming laptop",
        detail_page_status="bot_challenge",
    )
    raw = product.raw_payload or {}
    assert raw.get("source") == "listing_card"
    assert raw.get("detail_page_status") == "bot_challenge"
    assert raw["pdp_audit"]["badges_inspected"] is False
    assert raw["pdp_audit"]["badge_texts"] == []


def test_badges_table_written_for_newegg_notebook(session: Session) -> None:
    run = CollectionPersister(session).start_run(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _persist(session, _notebook(), run_id=int(run.id))
    assert session.scalar(select(func.count()).select_from(Badge)) >= 1
