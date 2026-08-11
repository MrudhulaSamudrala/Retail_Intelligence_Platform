"""Tests for platform / processor badge detection (per brand)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from collector.normalize import NormalizedProduct
from collector.parsers.badges import (
    STATUS_AMBIGUOUS,
    STATUS_CORRECT,
    STATUS_MISSING,
    BadgeEvidence,
    detect_badges_via_ocr,
    evaluate_badges,
    evaluation_rows,
    expected_badges,
)
from collector.persist import CollectionPersister
from database.models import Base
from database.repositories import ObservationRepository


# ---------------------------------------------------------------------------
# Expected badges from processor / product attributes
# ---------------------------------------------------------------------------


def test_intel_expected_core_ultra_not_plain_core() -> None:
    codes = expected_badges(processor="Intel Core Ultra 7 255H")
    assert "intel_core_ultra" in codes
    assert "intel_core" not in codes


def test_intel_expected_core_i_series() -> None:
    codes = expected_badges(title="Gaming Laptop Intel Core i7-13700H")
    assert codes == ["intel_core"]


def test_intel_expected_evo_and_vpro() -> None:
    codes = expected_badges(
        processor="Intel Core Ultra 5 125H",
        description="Intel Evo Edition with Intel vPro Essentials",
    )
    assert "intel_core_ultra" in codes
    assert "intel_evo" in codes
    assert "intel_vpro" in codes


def test_amd_expected_ryzen_ai_not_plain_ryzen() -> None:
    codes = expected_badges(processor="AMD Ryzen AI 9 HX 370")
    assert codes == ["amd_ryzen_ai"]


def test_amd_expected_plain_ryzen() -> None:
    codes = expected_badges(title="ASUS TUF Gaming AMD Ryzen 7 260")
    assert codes == ["amd_ryzen"]


def test_qualcomm_expected_snapdragon() -> None:
    codes = expected_badges(processor="Qualcomm Snapdragon X Elite")
    assert codes == ["qualcomm_snapdragon"]


def test_apple_expected_silicon_and_m_series() -> None:
    codes = expected_badges(title="Apple MacBook Pro", processor="Apple M3 Pro")
    assert "apple_silicon" in codes
    assert "apple_m_series" in codes


def test_apple_bare_m_without_context_not_expected() -> None:
    # Avoid false positives from unrelated "M3" tokens.
    assert expected_badges(title="Model M3 chassis kit") == []


# ---------------------------------------------------------------------------
# Per-brand evaluate: expected / detected / correct / missing / ambiguous
# ---------------------------------------------------------------------------


def test_intel_correct_core_ultra_badge() -> None:
    evaluation = evaluate_badges(
        processor="Intel Core Ultra 7 255H",
        evidence=BadgeEvidence(
            badge_texts=["Intel® Core™ Ultra"],
            img_alts=["Intel Core Ultra Technology"],
        ),
    )
    assert evaluation.expected == ["intel_core_ultra"]
    assert "intel_core_ultra" in evaluation.detected
    assert evaluation.correct == ["intel_core_ultra"]
    assert evaluation.missing == []
    assert evaluation.ambiguous == []


def test_intel_missing_and_ambiguous_evo() -> None:
    evaluation = evaluate_badges(
        processor="Intel Core Ultra 7",
        description="Intel Evo laptop",
        evidence=BadgeEvidence(
            badge_texts=["Intel® Core™ Ultra"],
            page_text="evo",  # bare token without badge context → ambiguous
        ),
    )
    assert "intel_core_ultra" in evaluation.correct
    assert "intel_evo" in evaluation.expected
    # Bare page_text "evo" should not count as confident detection.
    assert "intel_evo" not in evaluation.detected
    assert "intel_evo" in evaluation.missing or "intel_evo" in evaluation.ambiguous


def test_intel_vpro_detected_with_context() -> None:
    evaluation = evaluate_badges(
        processor="Intel Core i7 vPro",
        evidence=BadgeEvidence(badge_texts=["Intel® vPro® badge"]),
    )
    assert "intel_core" in evaluation.expected
    assert "intel_vpro" in evaluation.expected
    assert "intel_vpro" in evaluation.correct


def test_amd_ryzen_missing_badge() -> None:
    evaluation = evaluate_badges(
        processor="AMD Ryzen 9 8945HS",
        evidence=BadgeEvidence(badge_texts=["Best Seller"], img_alts=["product photo"]),
    )
    assert evaluation.expected == ["amd_ryzen"]
    assert evaluation.detected == []
    assert evaluation.missing == ["amd_ryzen"]
    assert evaluation.correct == []


def test_amd_ryzen_ai_correct() -> None:
    evaluation = evaluate_badges(
        title="HP OmniBook AMD Ryzen AI 7",
        processor="AMD Ryzen AI 7 350",
        evidence=BadgeEvidence(img_alts=["AMD Ryzen AI"], img_titles=["Ryzen AI badge"]),
    )
    assert evaluation.expected == ["amd_ryzen_ai"]
    assert evaluation.correct == ["amd_ryzen_ai"]
    assert evaluation.missing == []


def test_qualcomm_snapdragon_correct() -> None:
    evaluation = evaluate_badges(
        brand="Qualcomm",
        processor="Snapdragon X Plus",
        evidence=BadgeEvidence(element_texts=["Qualcomm Snapdragon"], badge_texts=[]),
    )
    assert evaluation.expected == ["qualcomm_snapdragon"]
    assert evaluation.correct == ["qualcomm_snapdragon"]


def test_apple_m_series_and_silicon() -> None:
    evaluation = evaluate_badges(
        title="Apple MacBook Air",
        processor="Apple M2",
        evidence=BadgeEvidence(
            badge_texts=["Apple Silicon", "Apple M2"],
            img_alts=["M-series badge"],
        ),
    )
    assert "apple_silicon" in evaluation.correct
    assert "apple_m_series" in evaluation.correct
    assert evaluation.missing == []


def test_apple_missing_silicon_when_only_m_badge() -> None:
    evaluation = evaluate_badges(
        title="MacBook Pro",
        processor="Apple M4 Pro",
        evidence=BadgeEvidence(img_alts=["Apple M4 Pro"]),
    )
    assert "apple_m_series" in evaluation.correct
    assert "apple_silicon" in evaluation.missing


def test_unexpected_detected_badge() -> None:
    evaluation = evaluate_badges(
        processor="AMD Ryzen 7 7735HS",
        evidence=BadgeEvidence(badge_texts=["Intel Core Ultra"]),
    )
    assert evaluation.expected == ["amd_ryzen"]
    assert "intel_core_ultra" in evaluation.unexpected
    assert "amd_ryzen" in evaluation.missing


def test_ocr_fallback_disabled_by_default() -> None:
    evidence = BadgeEvidence(ocr_texts=["Intel Core Ultra badge watermark"])
    assert detect_badges_via_ocr(evidence) == []
    enabled = detect_badges_via_ocr(evidence, enabled=True)
    assert any(h.code == "intel_core_ultra" and h.ambiguous for h in enabled)


def test_ocr_fills_gap_only_when_enabled() -> None:
    evaluation = evaluate_badges(
        processor="Intel Core Ultra 9",
        evidence=BadgeEvidence(ocr_texts=["Intel Core Ultra"]),
        use_ocr_fallback=True,
    )
    # OCR hits are ambiguous, so they do not become confident detections.
    assert "intel_core_ultra" in evaluation.ambiguous
    assert "intel_core_ultra" not in evaluation.detected
    assert evaluation.status_for("intel_core_ultra") == STATUS_AMBIGUOUS


def test_evaluation_rows_encode_statuses() -> None:
    evaluation = evaluate_badges(
        processor="AMD Ryzen 5",
        evidence=BadgeEvidence(badge_texts=[]),
    )
    rows = evaluation_rows(evaluation)
    assert len(rows) == 1
    assert rows[0]["badge_code"] == "amd_ryzen"
    assert rows[0]["status"] == STATUS_MISSING
    assert "status=missing" in rows[0]["relevance_notes"]


# ---------------------------------------------------------------------------
# Persistence into badges table
# ---------------------------------------------------------------------------


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()


def test_persist_platform_badges_for_each_brand(session: Session) -> None:
    persister = CollectionPersister(session)
    observations = ObservationRepository(session)
    run = persister.start_run(retailer_code="newegg", country_code="US", limit=4)
    session.commit()

    cases = [
        (
            NormalizedProduct(
                retailer_code="newegg",
                country_code="US",
                retailer_sku="BADGE-INTEL-1",
                source_url="https://www.newegg.com/p/BADGE-INTEL-1",
                title="MSI Stealth Intel Core Ultra 7",
                brand="Intel",
                oem="MSI",
                product_type="notebook",
                processor="Intel Core Ultra 7 255H",
                price_amount=Decimal("1499.00"),
                currency="USD",
            ),
            BadgeEvidence(badge_texts=["Intel® Core™ Ultra"]),
            "intel_core_ultra",
            STATUS_CORRECT,
        ),
        (
            NormalizedProduct(
                retailer_code="newegg",
                country_code="US",
                retailer_sku="BADGE-AMD-1",
                source_url="https://www.newegg.com/p/BADGE-AMD-1",
                title="ASUS TUF AMD Ryzen 9",
                brand="AMD",
                oem="Asus",
                product_type="notebook",
                processor="AMD Ryzen 9 8945HS",
                price_amount=Decimal("1299.00"),
                currency="USD",
            ),
            BadgeEvidence(badge_texts=[]),
            "amd_ryzen",
            STATUS_MISSING,
        ),
        (
            NormalizedProduct(
                retailer_code="newegg",
                country_code="US",
                retailer_sku="BADGE-QCOM-1",
                source_url="https://www.newegg.com/p/BADGE-QCOM-1",
                title="Lenovo Yoga Snapdragon X Elite",
                brand="Qualcomm",
                oem="Lenovo",
                product_type="notebook",
                processor="Qualcomm Snapdragon X Elite",
                price_amount=Decimal("1099.00"),
                currency="USD",
            ),
            BadgeEvidence(img_alts=["Snapdragon"]),
            "qualcomm_snapdragon",
            STATUS_CORRECT,
        ),
        (
            NormalizedProduct(
                retailer_code="newegg",
                country_code="US",
                retailer_sku="BADGE-APPLE-1",
                source_url="https://www.newegg.com/p/BADGE-APPLE-1",
                title="Apple MacBook Pro M3",
                brand="Apple",
                oem="Apple",
                product_type="notebook",
                processor="Apple M3 Pro",
                price_amount=Decimal("1999.00"),
                currency="USD",
            ),
            BadgeEvidence(badge_texts=["Apple Silicon", "Apple M3"]),
            "apple_silicon",
            STATUS_CORRECT,
        ),
    ]

    observed = datetime.now(timezone.utc)
    for product, evidence, code, status in cases:
        product_id = persister.save_product(
            product, collection_run_id=run.id, observed_at=observed
        )
        evaluation = persister.save_badges(
            product,
            product_id=product_id,
            collection_run_id=run.id,
            evidence=evidence,
            include_promotional=False,
            observed_at=observed,
        )
        assert evaluation.status_for(code) == status
        rows = observations.list_badges(product_id)
        assert rows
        assert any(r.badge_code == code for r in rows)
        assert any(r.is_relevant is True for r in rows)
        assert any(status in (r.relevance_notes or "") for r in rows)

    session.commit()
