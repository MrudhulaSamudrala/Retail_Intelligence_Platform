"""Unit tests for retailer audit checks S1, S2, P1–P5."""

from __future__ import annotations

from collector.audit import (
    FAIL,
    PASS,
    UNKNOWN,
    AuditContext,
    ListingEvidence,
    ProductEvidence,
    evaluate_all_checks,
    evaluate_p1,
    evaluate_p2,
    evaluate_p3,
    evaluate_p4,
    evaluate_p5,
    evaluate_s1,
    evaluate_s2,
)


def _ctx(
    *,
    brand: str | None = "Intel",
    oem: str | None = "MSI",
    listing: ListingEvidence | None = None,
    product: ProductEvidence | None = None,
) -> AuditContext:
    return AuditContext(
        retailer_code="newegg",
        country_code="US",
        brand=brand,
        oem=oem,
        product_type="notebook",
        listing=listing,
        product=product,
    )


# ---------------------------------------------------------------------------
# S1 — listing title brand / processor line
# ---------------------------------------------------------------------------


def test_s1_pass_brand_name_in_listing_title() -> None:
    result = evaluate_s1(
        _ctx(
            brand="AMD",
            listing=ListingEvidence(
                title="ASUS TUF Gaming A16 AMD Ryzen 7 260 RTX 5060",
                available=True,
                selectors_used=["a.item-title"],
                source_url="https://www.newegg.com/p/N82E16834236637",
            ),
        )
    )
    assert result.result == PASS
    assert result.check_code == "S1"
    assert "amd" in (result.evidence_text or "").lower() or "ryzen" in (
        result.evidence_text or ""
    ).lower()
    assert result.details.get("matched") is True


def test_s1_pass_processor_line_without_vendor_word() -> None:
    result = evaluate_s1(
        _ctx(
            brand="Intel",
            listing=ListingEvidence(title="MSI Stealth 16 Core Ultra 7 255H Gaming Laptop"),
        )
    )
    assert result.result == PASS
    assert result.details.get("processor_line_match")


def test_s1_fail_title_present_without_brand_or_processor() -> None:
    result = evaluate_s1(
        _ctx(
            brand="Intel",
            listing=ListingEvidence(title="MSI 16 inch Gaming Notebook 32GB RAM 1TB SSD"),
        )
    )
    assert result.result == FAIL
    assert result.details.get("matched") is False


def test_s1_unknown_when_listing_missing_or_brand_unknown() -> None:
    assert evaluate_s1(_ctx(listing=None)).result == UNKNOWN
    assert (
        evaluate_s1(
            _ctx(brand="UNKNOWN", listing=ListingEvidence(title="Something"))
        ).result
        == UNKNOWN
    )
    assert (
        evaluate_s1(
            _ctx(brand="Intel", listing=ListingEvidence(title=None, available=True))
        ).result
        == UNKNOWN
    )


# ---------------------------------------------------------------------------
# S2 — listing brand badge
# ---------------------------------------------------------------------------


def test_s2_pass_when_brand_badge_text_present() -> None:
    result = evaluate_s2(
        _ctx(
            brand="Intel",
            listing=ListingEvidence(
                title="MSI Stealth",
                badge_texts=["Intel® Core™ Ultra Badge"],
                selectors_used=[".item-brand"],
            ),
        )
    )
    assert result.result == PASS
    assert result.details.get("matched") is True


def test_s2_fail_when_badges_inspected_but_absent() -> None:
    result = evaluate_s2(
        _ctx(
            brand="AMD",
            listing=ListingEvidence(
                title="ASUS TUF AMD Ryzen 7",
                badge_texts=[],
                tile_text="Free shipping Compare Add to cart",
            ),
        )
    )
    assert result.result == FAIL


def test_s2_unknown_without_badge_evidence() -> None:
    result = evaluate_s2(
        _ctx(
            brand="Intel",
            listing=ListingEvidence(title="MSI Laptop", badge_texts=[], tile_text=None),
        )
    )
    assert result.result == UNKNOWN
    assert result.details.get("reason") == "listing_badge_evidence_missing"


def test_s2_hdmi_does_not_create_hp_badge_for_intel_product() -> None:
    # Sanity: auditing Intel brand; HDMI text must not invent an HP badge match path.
    result = evaluate_s2(
        _ctx(
            brand="Intel",
            listing=ListingEvidence(
                title="MSI Stealth HDMI",
                badge_texts=["HDMI 2.1"],
                tile_text="HDMI ports",
            ),
        )
    )
    assert result.result == FAIL


# ---------------------------------------------------------------------------
# P1 — product title
# ---------------------------------------------------------------------------


def test_p1_pass_and_fail_and_unknown() -> None:
    pass_result = evaluate_p1(
        _ctx(
            brand="Intel",
            product=ProductEvidence(
                title="Dell Alienware Intel Core Ultra 9 275HX",
                available=True,
            ),
        )
    )
    assert pass_result.result == PASS

    fail_result = evaluate_p1(
        _ctx(
            brand="Qualcomm",
            product=ProductEvidence(title="ACEMAGIC 16 Inch Gray Laptop 24GB RAM"),
        )
    )
    assert fail_result.result == FAIL

    unknown_result = evaluate_p1(_ctx(product=None))
    assert unknown_result.result == UNKNOWN


# ---------------------------------------------------------------------------
# P2 — product brand badge
# ---------------------------------------------------------------------------


def test_p2_pass_fail_unknown() -> None:
    assert (
        evaluate_p2(
            _ctx(
                brand="AMD",
                product=ProductEvidence(
                    title="ASUS TUF",
                    badge_texts=["AMD Ryzen"],
                    badges_inspected=True,
                ),
            )
        ).result
        == PASS
    )
    assert (
        evaluate_p2(
            _ctx(
                brand="AMD",
                product=ProductEvidence(
                    title="ASUS TUF",
                    badge_texts=[],
                    badges_inspected=True,
                    page_text="Add to cart Free shipping",
                ),
            )
        ).result
        == FAIL
    )
    assert (
        evaluate_p2(
            _ctx(
                brand="AMD",
                product=ProductEvidence(
                    title="ASUS TUF",
                    badges_inspected=False,
                    badge_texts=[],
                    page_text=None,
                ),
            )
        ).result
        == UNKNOWN
    )


def test_p2_listing_title_is_not_pdp_badge_evidence() -> None:
    """Missing PDP badge inspection must be UNKNOWN — never FAIL from title text."""
    result = evaluate_p2(
        _ctx(
            brand="AMD",
            product=ProductEvidence(
                title="Notebook ASUS AMD Ryzen 7",
                badges_inspected=False,
                badge_texts=[],
                page_text="Notebook ASUS AMD Ryzen 7",  # listing title wrongly promoted
            ),
        )
    )
    assert result.result == UNKNOWN
    assert result.details.get("reason") == "product_badge_evidence_missing"


def test_p2_account_verification_fallback_is_unknown() -> None:
    from collector.audit.run_mercadolibre_existing import evidence_from_stored_product

    listing, product = evidence_from_stored_product(
        {
            "title": "Notebook ASUS Vivobook AMD Ryzen 7 8GB",
            "canonical_url": "https://www.mercadolivre.com.br/p/MLB1",
        }
    )
    assert product.page_text is None
    assert product.badges_inspected is False
    assert product.specs_available is False
    assert listing.tile_text is None
    ctx = _ctx(brand="AMD", oem="Asus", listing=listing, product=product)
    assert evaluate_p2(ctx).result == UNKNOWN
    assert evaluate_p1(ctx).result in {PASS, FAIL}  # title evidence allowed for P1
    assert evaluate_p3(ctx).result == UNKNOWN



# ---------------------------------------------------------------------------
# P3 — specification table
# ---------------------------------------------------------------------------


def test_p3_pass_fail_unknown() -> None:
    assert (
        evaluate_p3(
            _ctx(
                brand="Intel",
                product=ProductEvidence(
                    title="MSI Vector",
                    specs={"CPU": "Intel Core Ultra 7 255HX", "Memory": "32GB"},
                    specs_available=True,
                    selectors_used=["#product-details table tr"],
                ),
            )
        ).result
        == PASS
    )
    assert (
        evaluate_p3(
            _ctx(
                brand="Intel",
                product=ProductEvidence(
                    title="MSI Vector",
                    specs={"Memory": "32GB", "Storage": "1TB"},
                    specs_available=True,
                ),
            )
        ).result
        == FAIL
    )
    assert (
        evaluate_p3(
            _ctx(
                brand="Intel",
                product=ProductEvidence(
                    title="MSI Vector",
                    specs={},
                    specs_available=False,
                ),
            )
        ).result
        == UNKNOWN
    )


# ---------------------------------------------------------------------------
# P4 — brand-led rich media
# ---------------------------------------------------------------------------


def test_p4_pass_fail_unknown() -> None:
    assert (
        evaluate_p4(
            _ctx(
                brand="Intel",
                product=ProductEvidence(
                    title="MSI",
                    media_inspected=True,
                    brand_media_signals=[
                        "img alt=Intel Core Ultra Technology",
                        "gallery/intel-feature.png",
                    ],
                ),
            )
        ).result
        == PASS
    )
    assert (
        evaluate_p4(
            _ctx(
                brand="Intel",
                product=ProductEvidence(
                    title="MSI",
                    media_inspected=True,
                    brand_media_signals=["product front view", "box contents"],
                ),
            )
        ).result
        == FAIL
    )
    assert (
        evaluate_p4(
            _ctx(
                brand="Intel",
                product=ProductEvidence(title="MSI", media_inspected=False),
            )
        ).result
        == UNKNOWN
    )


# ---------------------------------------------------------------------------
# P5 — OEM rich media
# ---------------------------------------------------------------------------


def test_p5_pass_fail_unknown() -> None:
    assert (
        evaluate_p5(
            _ctx(
                oem="Asus",
                product=ProductEvidence(
                    title="ASUS TUF",
                    media_inspected=True,
                    oem_media_signals=["ASUS ROG logo banner", "img alt=ASUS TUF"],
                ),
            )
        ).result
        == PASS
    )
    assert (
        evaluate_p5(
            _ctx(
                oem="Asus",
                product=ProductEvidence(
                    title="ASUS TUF",
                    media_inspected=True,
                    oem_media_signals=["generic stock photo"],
                ),
            )
        ).result
        == FAIL
    )
    assert (
        evaluate_p5(
            _ctx(
                oem="UNKNOWN",
                product=ProductEvidence(
                    title="ACEMAGIC",
                    media_inspected=True,
                    oem_media_signals=["ACEMAGIC logo"],
                ),
            )
        ).result
        == UNKNOWN
    )
    assert (
        evaluate_p5(
            _ctx(
                oem="Dell",
                product=ProductEvidence(title="Dell", media_inspected=False),
            )
        ).result
        == UNKNOWN
    )


# ---------------------------------------------------------------------------
# Independence + full suite
# ---------------------------------------------------------------------------


def test_each_check_is_independent_in_evaluate_all() -> None:
    results = evaluate_all_checks(
        _ctx(
            brand="AMD",
            oem="Asus",
            listing=ListingEvidence(
                title="ASUS TUF AMD Ryzen 7",
                badge_texts=[],
                tile_text="no brand badge here",
            ),
            product=ProductEvidence(
                title="ASUS TUF Gaming AMD Ryzen 7 260",
                specs={"CPU": "AMD Ryzen 7 260"},
                specs_available=True,
                badge_texts=["AMD Ryzen"],
                badges_inspected=True,
                media_inspected=True,
                brand_media_signals=["amd ryzen feature image"],
                oem_media_signals=["asus tuf gallery"],
            ),
        )
    )
    by_code = {r.check_code: r.result for r in results}
    assert set(by_code) == {"S1", "S2", "P1", "P2", "P3", "P4", "P5"}
    assert by_code["S1"] == PASS
    assert by_code["S2"] == FAIL  # badges inspected, none matched
    assert by_code["P1"] == PASS
    assert by_code["P2"] == PASS
    assert by_code["P3"] == PASS
    assert by_code["P4"] == PASS
    assert by_code["P5"] == PASS


def test_missing_information_never_becomes_pass() -> None:
    results = evaluate_all_checks(
        _ctx(brand="Intel", oem="MSI", listing=None, product=None)
    )
    assert all(r.result == UNKNOWN for r in results)
    assert PASS not in {r.result for r in results}


def test_audit_results_persist_to_retailer_audits(tmp_path=None) -> None:
    """Engine appends one retailer_audits row per check with evidence preserved."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from collector.audit.engine import evaluate_and_persist
    from collector.normalize import NormalizedProduct
    from database.models import Base, RetailerAudit
    from database.repositories import CollectionRunRepository, ProductRepository

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    session = SessionLocal()
    try:
        run = CollectionRunRepository(session).start(
            retailer_code="newegg",
            country_code="US",
            run_type="audit",
        )
        product = ProductRepository(session).upsert_identity(
            retailer_code="newegg",
            country_code="US",
            retailer_sku="TEST-AUDIT-1",
            canonical_url="https://www.newegg.com/p/TEST-AUDIT-1",
            title="ASUS TUF AMD Ryzen 7",
            brand="AMD",
            oem="Asus",
            product_type="notebook",
        )
        session.commit()

        ctx = AuditContext(
            retailer_code="newegg",
            country_code="US",
            brand="AMD",
            oem="Asus",
            product_type="notebook",
            product_id=product.id,
            collection_run_id=run.id,
            listing=ListingEvidence(
                title="ASUS TUF AMD Ryzen 7",
                badge_texts=["AMD Ryzen"],
                tile_text="AMD Ryzen badge",
                source_url="https://www.newegg.com/p/pl",
            ),
            product=ProductEvidence(
                title="ASUS TUF Gaming AMD Ryzen 7 260",
                specs={"CPU": "AMD Ryzen 7 260"},
                specs_available=True,
                badge_texts=["AMD Ryzen"],
                badges_inspected=True,
                media_inspected=True,
                brand_media_signals=["amd feature"],
                oem_media_signals=["asus gallery"],
                source_url="https://www.newegg.com/p/TEST-AUDIT-1",
            ),
        )
        results = evaluate_and_persist(session, ctx)
        session.commit()

        rows = session.scalars(select(RetailerAudit).order_by(RetailerAudit.check_code)).all()
        assert len(rows) == 7
        assert {r.check_code for r in rows} == {"S1", "S2", "P1", "P2", "P3", "P4", "P5"}
        assert all(r.result in {PASS, FAIL, UNKNOWN} for r in rows)
        assert all(r.product_id == product.id for r in rows)
        assert all(r.details is not None for r in rows)
        assert {r.check_code: r.result for r in rows} == {
            item.check_code: item.result for item in results
        }
    finally:
        session.close()
        engine.dispose()
