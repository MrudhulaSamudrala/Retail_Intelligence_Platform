"""Live classifier output must be persisted; stratum is discovery metadata only."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from collector.classification import OTHER, UNKNOWN
from collector.normalize import build_normalized_product
from collector.observation import (
    STATUS_EXCLUDED,
    STATUS_VALID,
    apply_live_classification,
    observation_bucket,
)
from collector.persist import CollectionPersister
from collector.retailers.mercadolibre.classification import EXCLUDED, VALID
from database.models import Base, Product, ProductSnapshot


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


def _product(*, title: str, sku: str, stratum: str, **kwargs):
    payload = dict(kwargs.pop("raw_payload", None) or {})
    payload.setdefault("product_type_hint", stratum)
    product = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku=sku,
        source_url=f"https://www.newegg.com/p/{sku}",
        title=title,
        category_raw=stratum,
        raw_payload=payload,
        **kwargs,
    )
    # Simulate a stale SERP-hint classification that must not survive persist.
    product.product_type = stratum if stratum != "cpu" else "desktop"
    product.raw_payload["classification"] = {
        "status": VALID,
        "product_type": product.product_type,
        "gaming": True,
        "hard_negative": False,
        "reasons": ["stale_stratum_hint"],
    }
    return product


def _persist(session: Session, product) -> Product:
    apply_live_classification(product)
    persister = CollectionPersister(session)
    run = persister.start_run(retailer_code="newegg", country_code="US", run_type="pricing")
    product_id = persister.save_product(
        product,
        collection_run_id=int(run.id),
        observed_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
    )
    session.flush()
    return session.get(Product, product_id)


def test_cpu_stratum_ryzen_processor_persists_cpu(session: Session) -> None:
    product = _product(
        title="AMD Ryzen 7 9700X Socket AM5 65W Desktop Processor",
        sku="CPU-AMD",
        stratum="cpu",
        processor="AMD Ryzen 7 9700X",
    )
    row = _persist(session, product)
    assert product.product_type == "cpu"
    assert row.product_type == "cpu"
    assert row.brand == "AMD"
    assert observation_bucket(product) == STATUS_VALID


def test_cpu_stratum_intel_processor_persists_cpu(session: Session) -> None:
    product = _product(
        title="Intel Core i7-14700K Desktop Processor Unlocked",
        sku="CPU-INT",
        stratum="cpu",
        processor="Intel Core i7-14700K",
    )
    row = _persist(session, product)
    assert row.product_type == "cpu"
    assert row.brand == "Intel"


def test_gpu_stratum_radeon_persists_gpu_amd(session: Session) -> None:
    product = _product(
        title="GIGABYTE Gaming Radeon RX 9070 GRE 12GB GDDR6 Graphics Card",
        sku="GPU-RAD",
        stratum="gpu",
        gpu="Radeon RX 9070 GRE",
    )
    product.product_type = "desktop"
    row = _persist(session, product)
    assert row.product_type == "gpu"
    assert row.brand == "AMD"


def test_gpu_stratum_geforce_persists_gpu_other(session: Session) -> None:
    product = _product(
        title="Yeston GeForce RTX 5060 Ti 16GB GDDR7 Graphics Card",
        sku="GPU-NV",
        stratum="gpu",
        gpu="GeForce RTX 5060 Ti",
    )
    product.product_type = "desktop"
    row = _persist(session, product)
    assert row.product_type == "gpu"
    assert row.brand == OTHER


def test_laptop_stratum_intel_laptop_persists_notebook_intel(session: Session) -> None:
    product = _product(
        title="ASUS TUF Gaming Laptop Intel Core Ultra 7 16GB",
        sku="NB-INT",
        stratum="notebook",
        processor="Intel Core Ultra 7",
    )
    row = _persist(session, product)
    assert row.product_type == "notebook"
    assert row.brand == "Intel"
    assert row.oem == "Asus"


def test_laptop_with_rtx_keeps_intel_platform_brand(session: Session) -> None:
    product = _product(
        title="MSI Vector 16 Intel Core Ultra 7 NVIDIA GeForce RTX 5070 Ti Gaming Laptop",
        sku="NB-RTX",
        stratum="notebook",
        processor="Intel Core Ultra 7",
        gpu="GeForce RTX 5070 Ti",
    )
    row = _persist(session, product)
    assert row.product_type == "notebook"
    assert row.brand == "Intel"
    assert row.brand != OTHER


def test_desktop_stratum_actual_pc_persists_desktop(session: Session) -> None:
    product = _product(
        title="MSI MAG Infinite RS Gaming Desktop PC AMD Ryzen 7 7700 RTX 4060",
        sku="DT-1",
        stratum="desktop",
        processor="AMD Ryzen 7 7700",
    )
    row = _persist(session, product)
    assert row.product_type == "desktop"
    assert row.brand == "AMD"


def test_workstation_stratum_actual_workstation_persists_workstation(
    session: Session,
) -> None:
    product = _product(
        title="Lenovo ThinkStation P3 Tower Intel Xeon gaming workstation",
        sku="WS-1",
        stratum="workstation",
        processor="Intel Xeon",
    )
    row = _persist(session, product)
    assert row.product_type == "workstation"
    assert row.brand == "Intel"
    assert row.oem == "Lenovo"


def test_workstation_stratum_standing_desk_persists_other_excluded(
    session: Session,
) -> None:
    product = _product(
        title='E6G CyberX RGB LED Electric Gaming Standing Desk, 55 Inch',
        sku="DESK-1",
        stratum="workstation",
    )
    apply_live_classification(product)
    assert product.product_type == "other"
    assert product.raw_payload["classification"]["status"] == EXCLUDED
    assert product.raw_payload["classification"]["gaming"] is False
    assert observation_bucket(product) == STATUS_EXCLUDED
    row = _persist(session, product)
    assert row.product_type == "other"
    snap = session.scalars(select(ProductSnapshot)).first()
    assert snap is not None
    assert snap.product_type == "other"


def test_tablet_stratum_ipad_without_soc_unknown_brand_apple_oem(
    session: Session,
) -> None:
    product = _product(
        title='Apple iPad 7 (7th Gen) 32GB - Wi-Fi - 10.2" - Space Gray',
        sku="IPAD-1",
        stratum="tablet",
    )
    row = _persist(session, product)
    assert row.product_type == "tablet"
    assert row.brand == UNKNOWN
    assert row.oem == "Apple"


def test_ipad_with_apple_silicon_is_brand_apple(session: Session) -> None:
    product = _product(
        title="Apple iPad Pro 11-inch M4",
        sku="IPAD-M",
        stratum="tablet",
        processor="Apple M4",
    )
    row = _persist(session, product)
    assert row.product_type == "tablet"
    assert row.brand == "Apple"
    assert row.oem == "Apple"


def test_niakun_office_365_does_not_become_apple_oem(session: Session) -> None:
    title = (
        "NIAKUN Gaming Laptop,2026 Laptops for Gaming,Windows 11 Pro Lap Top "
        "Computer with Office 365,16GB RAM 512GB SSD,Intel Core i7 Processor"
        '(Up to 3.9GHz),15.6" IPS FHD,5000mAh,Backlit Keyboard'
    )
    product = _product(title=title, sku="NIAKUN-1", stratum="notebook")
    product.oem = "Apple"
    row = _persist(session, product)
    assert row.brand == "Intel"
    assert row.oem != "Apple"
    assert row.product_type == "notebook"


def test_snapdragon_evidence_persists_qualcomm(session: Session) -> None:
    product = _product(
        title="Lenovo Yoga Slim 7x Snapdragon X Elite Laptop",
        sku="QC-1",
        stratum="notebook",
        processor="Snapdragon X Elite",
    )
    row = _persist(session, product)
    assert row.brand == "Qualcomm"
    assert row.product_type == "notebook"


def test_no_processor_evidence_stays_unknown_brand(session: Session) -> None:
    product = _product(
        title="15 Inch Gaming Notebook 16GB RAM 512GB SSD",
        sku="UNK-1",
        stratum="notebook",
    )
    row = _persist(session, product)
    assert row.brand == UNKNOWN
    assert row.product_type == "notebook"


def test_stratum_never_overrides_product_type() -> None:
    product = _product(
        title="ASUS TUF Gaming Laptop Intel Core i7 RTX 4060",
        sku="HINT-1",
        stratum="gpu",
        processor="Intel Core i7",
    )
    apply_live_classification(product)
    assert product.category_raw == "gpu"
    assert product.raw_payload.get("product_type_hint") == "gpu"
    assert product.product_type == "notebook"
    assert product.product_type != "gpu"


def test_persisted_product_type_equals_classifier_output(session: Session) -> None:
    product = _product(
        title="AMD Ryzen 7 7800X3D Processor Socket AM5",
        sku="EQ-1",
        stratum="desktop",
        processor="AMD Ryzen 7 7800X3D",
    )
    apply_live_classification(product)
    clf_type = product.raw_payload["classification"]["product_type"]
    row = _persist(session, product)
    assert row.product_type == clf_type == "cpu"
    assert row.product_type != "desktop"
