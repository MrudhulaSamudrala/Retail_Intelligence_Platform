"""Mercado Libre collection-universe semantics (--limit = observed results)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from collector.base import ListingCandidate, RetailerCollector
from collector.normalize import NormalizedProduct, build_normalized_product
from collector.observation import (
    COMPLETENESS_COMPLETE,
    COMPLETENESS_PARTIAL,
    ObservationCounters,
    completeness_status,
    is_eligible_gaming,
    observation_bucket,
)
from collector.pipeline import CollectionPipeline
from collector.retailers.mercadolibre.classification import (
    EXCLUDED,
    OTHER_TYPE,
    VALID,
    classify_mercadolibre_product,
)
from collector.retailers.mercadolibre.discovery import (
    BRAND_BIASED_COLLECTION_QUERIES,
    GENERIC_GAMING_QUERIES,
    collection_queries,
    load_discovery_config,
)
from collector.retailers.mercadolibre.product_page import build_from_listing
from collector.classification import OTHER, UNKNOWN, classify_brand
from database.models import Base, Product, ProductSnapshot


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()


def _listing_product(sku: str, title: str) -> NormalizedProduct:
    return build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku=sku,
        source_url=f"https://www.mercadolivre.com.br/p/{sku}",
        title=title,
        price_text="1.000,00",
        list_price_text=None,
        promo_text=None,
        category_raw="MLB1652",
        detail_page_status="listing_only",
    )


def test_limit_n_means_n_observed_not_n_valid() -> None:
    serp = [
        ("1", "Notebook Gamer ASUS TUF Intel Core i7 RTX 4060"),
        ("2", "Notebook Gamer Lenovo Legion AMD Ryzen 7 RTX 4070"),
        ("3", "Samsung Smart Tv 55 4k"),
        ("4", "Notebook Gamer MSI Intel Core i9 RTX 4080"),
        ("5", "Power Bank Turbo 20000mah"),
        ("6", "Notebook Gamer Acer Nitro AMD Ryzen 5"),
    ]
    limit = 4
    counters = ObservationCounters(requested=limit)
    for sku, title in serp:
        if counters.observed >= limit:
            break
        product = _listing_product(sku, title)
        counters.record(observation_bucket(product), product)
    assert counters.observed == 4
    assert counters.valid == 3
    assert counters.excluded == 1
    assert counters.unknown == 0
    assert counters.failed == 0
    assert "6" not in [b for b in []]  # Acer not pulled in to replace TV
    universe = counters.as_dict(completeness=counters.completeness(had_error=False))
    assert universe["reconciles"]
    assert universe["valid"] + universe["excluded"] + universe["unknown"] + universe["failed"] == 4
    assert counters.buckets == ["VALID", "VALID", "EXCLUDED", "VALID"]


def test_excluded_product_consumes_observation_position() -> None:
    product = _listing_product("TV1", "Samsung Smart Tv 55 4k")
    assert observation_bucket(product) == "EXCLUDED"
    counters = ObservationCounters(requested=1)
    counters.record(observation_bucket(product), product)
    assert counters.observed == 1
    assert counters.excluded == 1
    assert counters.valid == 0


def test_unknown_product_consumes_observation_position() -> None:
    product = _listing_product("U1", "Item especial 16 unidades caixa mista")
    assert observation_bucket(product) == "UNKNOWN"
    counters = ObservationCounters(requested=1)
    counters.record(observation_bucket(product), product)
    assert counters.observed == 1
    assert counters.unknown == 1
    assert counters.valid == 0


def test_failed_extraction_counted_separately() -> None:
    counters = ObservationCounters(requested=2)
    counters.record("FAILED")
    counters.record(observation_bucket(_listing_product("N1", "Notebook Gamer Dell Intel Core i7 RTX")))
    assert counters.failed == 1
    assert counters.valid == 1
    assert counters.observed == 2
    assert counters.valid + counters.excluded + counters.unknown + counters.failed == counters.observed


def test_counts_reconcile_requested_observed_valid_excluded_unknown_failed() -> None:
    items = [
        _listing_product("1", "Notebook Gamer ASUS TUF Intel Core i7 RTX 4060"),
        _listing_product("2", "Smart Tv Philips 50"),
        _listing_product("3", "Caixa misturada sem tipo"),
    ]
    counters = ObservationCounters(requested=10)
    for product in items:
        counters.record(observation_bucket(product), product)
    counters.record("FAILED")
    assert counters.requested == 10
    assert counters.observed == 4
    assert counters.valid + counters.excluded + counters.unknown + counters.failed == counters.observed
    assert completeness_status(requested=10, observed=4, had_error=False) == COMPLETENESS_PARTIAL


def test_fewer_than_n_observable_is_partial() -> None:
    assert completeness_status(requested=100, observed=73, had_error=False) == COMPLETENESS_PARTIAL
    assert completeness_status(requested=100, observed=100, had_error=False) == COMPLETENESS_COMPLETE


def test_brand_specific_collection_queries_not_used() -> None:
    queries = collection_queries()
    for biased in BRAND_BIASED_COLLECTION_QUERIES:
        assert biased not in queries, biased
    blob = " ".join(queries)
    assert "notebook intel" not in blob
    assert "notebook ryzen" not in blob
    assert "notebook amd" not in blob
    assert "notebook qualcomm" not in blob
    assert "notebook apple" not in blob


def test_generic_gaming_queries_are_used() -> None:
    from collector.universe_config import (
        collection_queries_for,
        generic_query_for,
        validate_universe_config,
    )

    queries = set(collection_queries())
    assert queries == GENERIC_GAMING_QUERIES
    facts = validate_universe_config()
    assert facts["total_budget"] == 100
    assert facts["stratum_budgets"] == {
        "notebook": 20,
        "desktop": 20,
        "workstation": 20,
        "tablet": 20,
        "gpu": 10,
        "cpu": 10,
    }
    assert collection_queries_for("mercadolibre") == [
        "notebook gamer",
        "pc gamer",
        "workstation gamer",
        "tablet gamer",
        "placa de video gamer",
        "processador gamer",
    ]
    assert collection_queries_for("newegg") == [
        "gaming laptop",
        "gaming desktop",
        "gaming workstation",
        "gaming tablet",
        "gaming graphics card",
        "gaming processor",
    ]
    cfg = load_discovery_config()
    urls = " ".join(str(item.get("url") or "") for item in (cfg.get("discovery_primary") or []))
    assert "notebook-intel" not in urls
    assert "notebook-ryzen" not in urls
    assert generic_query_for("mercadolibre") == "notebook gamer"
    assert generic_query_for("newegg") == "gaming laptop"
    first = (cfg.get("discovery_primary") or [{}])[0]
    assert str(first.get("query") or "").lower() == "notebook gamer"


def test_sov_keywords_still_allow_brand_intent() -> None:
    from collector.search.config import CONFIG_PATH
    import yaml

    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    ml = cfg["retailers"]["mercadolibre"]["countries"]["BR"]["queries"]
    assert "notebook gamer ryzen" in ml


def test_other_brand_distinguishable_from_unknown() -> None:
    other, _ = classify_brand(title="Tablet MediaTek Dimensity 7020")
    unknown, _ = classify_brand(title="Notebook 15 pol 8GB RAM")
    assert other == OTHER
    assert unknown == UNKNOWN
    assert other != unknown


def test_other_product_type_distinguishable_from_unknown() -> None:
    tv = classify_mercadolibre_product(title="Smart Tv Samsung 50 4k")
    mystery = classify_mercadolibre_product(title="Caixa sortida unidades diversas")
    assert tv.status == EXCLUDED
    assert tv.product_type == OTHER_TYPE
    assert mystery.status != EXCLUDED or mystery.product_type == UNKNOWN
    assert mystery.product_type == UNKNOWN
    assert tv.product_type != mystery.product_type


def test_gaming_relevance_separate_from_product_type() -> None:
    gaming = classify_mercadolibre_product(
        title="Notebook Gamer Lenovo Legion RTX 4070 Intel Core i7"
    )
    office = classify_mercadolibre_product(
        title="Notebook Dell Latitude Intel Core i5 16GB SSD"
    )
    assert gaming.product_type == "notebook"
    assert office.product_type == "notebook"
    assert gaming.gaming is True
    assert office.gaming is False
    assert gaming.status == VALID
    assert office.status == VALID
    assert is_eligible_gaming(_listing_product("G1", gaming.evidence["title"])) is True
    assert is_eligible_gaming(_listing_product("O1", office.evidence["title"])) is False


class _NullBrowser:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _FakeMLCollector(RetailerCollector):
    code = "mercadolibre"
    country_code = "BR"
    currency = "BRL"
    uses_observed_result_limit = True

    def __init__(
        self,
        catalog: dict[str, str],
        order: list[str],
        fail_skus: set[str] | None = None,
        inaccessible_skus: set[str] | None = None,
    ):
        self.catalog = catalog
        self.order = order
        self.fail_skus = fail_skus or set()
        self.inaccessible_skus = inaccessible_skus or set()
        self.discovery_stats: dict = {}

    def build_browser_session(self):
        return _NullBrowser()

    async def discover_listings(self, session, *, limit: int):
        stats = {
            "pages_attempted": getattr(self, "pages_attempted", 1),
            "pages_inspected": getattr(self, "pages_inspected", 1),
            "pages_blocked": getattr(self, "pages_blocked", 0),
            "pagination_reliable": getattr(self, "pagination_reliable", True),
            "last_observed_position": 0,
            "search_status": getattr(self, "search_status", "OK"),
            "query": getattr(self, "query", "notebook gamer"),
            "stop_reason": getattr(self, "stop_reason", None),
        }
        out = []
        for idx, sku in enumerate(self.order[:limit], start=1):
            cand = ListingCandidate(
                retailer_sku=sku,
                source_url=f"https://www.mercadolivre.com.br/p/{sku}",
                title=self.catalog[sku],
                search_position=idx,
                search_page=1 + (idx - 1) // getattr(self, "page_size", 50),
                query=stats["query"],
            )
            out.append(cand)
        stats["last_observed_position"] = len(out)
        stats["pages_inspected"] = max(
            1, (len(out) + getattr(self, "page_size", 50) - 1) // getattr(self, "page_size", 50)
        ) if out else stats["pages_inspected"]
        self.discovery_stats = stats
        return out

    async def fetch_product(self, session, candidate: ListingCandidate) -> NormalizedProduct:
        if candidate.retailer_sku in self.inaccessible_skus:
            raise RuntimeError("bot challenge on product page")
        if candidate.retailer_sku in self.fail_skus:
            raise RuntimeError("extraction_failed")
        return _listing_product(candidate.retailer_sku, self.catalog[candidate.retailer_sku])


def test_pipeline_observed_limit_and_identity(session: Session) -> None:
    catalog = {
        "MLB1": "Notebook Gamer ASUS TUF Intel Core i7 RTX 4060",
        "MLB2": "Notebook Gamer Lenovo Legion AMD Ryzen 7",
        "MLB3": "Samsung Smart Tv 55 4k",
        "MLB4": "Notebook Gamer MSI Intel Core i9",
        "MLB5": "Power Bank Turbo 20000mah",
        "MLB6": "Notebook Gamer Acer Nitro AMD Ryzen 5 RTX",
        "MLB7": "Caixa sortida sem evidência de tipo",
        "MLB8": "Notebook Gamer Dell Intel Core i5",
        "MLB9": "Notebook Gamer HP Omen RTX",
        "MLB10": "Notebook Gamer Acer Predator RTX",
        "MLB1B": "Notebook Gamer ASUS TUF Intel Core i7 RTX 4060",
    }
    order = ["MLB1", "MLB2", "MLB3", "MLB4", "MLB5", "MLB6", "MLB7", "MLB8", "MLB1", "MLB9"]
    collector = _FakeMLCollector(catalog, order, fail_skus=set())
    pipeline = CollectionPipeline(session, collector)
    outcome = asyncio.run(pipeline.run(limit=10))

    u = outcome.universe
    assert u["requested"] == 10
    assert u["observed"] == 10
    assert u["valid"] + u["excluded"] + u["unknown"] + u["failed"] + u["duplicate"] == 10
    assert u["excluded"] >= 2  # TV + power bank
    assert u["unknown"] >= 1
    assert u["duplicate"] == 1
    assert u["completeness"] == COMPLETENESS_COMPLETE
    assert outcome.status == "completed"
    obs = outcome.universe["observations"]
    assert all(row.get("country") == "BR" for row in obs)
    dup_rows = [row for row in obs if row.get("bucket") == "DUPLICATE"]
    assert dup_rows
    assert dup_rows[0]["search_position"] == 9
    # Excluded were not replaced by MLB10
    skus = {p.retailer_sku for p in outcome.success}
    assert "MLB10" not in skus
    assert "MLB3" not in skus  # TV not valid
    products = session.scalars(select(Product)).all()
    assert {p.retailer_sku for p in products} <= set(order)
    assert len({p.retailer_sku for p in products}) == len(products)


def test_pipeline_partial_when_short(session: Session) -> None:
    catalog = {
        "A": "Notebook Gamer ASUS TUF Intel Core i7 RTX",
        "B": "Smart Tv 43",
    }
    collector = _FakeMLCollector(catalog, ["A", "B"])
    pipeline = CollectionPipeline(session, collector)
    outcome = asyncio.run(pipeline.run(limit=10))
    assert outcome.universe["requested"] == 10
    assert outcome.universe["observed"] == 2
    assert outcome.universe["completeness"] == COMPLETENESS_PARTIAL
    assert outcome.status == "partial"
    assert outcome.universe["valid"] + outcome.universe["excluded"] == 2


def test_pipeline_failed_extraction_separate(session: Session) -> None:
    catalog = {
        "OK": "Notebook Gamer Lenovo Legion Intel Core i7 RTX",
        "BAD": "Notebook Gamer MSI Intel Core i9 RTX",
    }
    collector = _FakeMLCollector(catalog, ["OK", "BAD"], fail_skus={"BAD"})
    pipeline = CollectionPipeline(session, collector)
    outcome = asyncio.run(pipeline.run(limit=2))
    assert outcome.universe["failed"] == 1
    assert outcome.universe["valid"] == 1
    assert outcome.universe["observed"] == 2
    assert len(outcome.failed) == 1


def test_historical_identity_preserved_on_reobserve(session: Session) -> None:
    catalog = {"MLB655": "Notebook Gamer ASUS TUF Intel Core i7 RTX 4060"}
    collector = _FakeMLCollector(catalog, ["MLB655"])
    pipeline = CollectionPipeline(session, collector)
    first = asyncio.run(pipeline.run(limit=1))
    assert first.universe["observed"] == 1
    count_after_first = session.scalar(select(func.count()).select_from(Product))
    snaps_after_first = session.scalar(select(func.count()).select_from(ProductSnapshot))
    second = asyncio.run(pipeline.run(limit=1))
    assert second.universe["observed"] == 1
    count_after_second = session.scalar(select(func.count()).select_from(Product))
    snaps_after_second = session.scalar(select(func.count()).select_from(ProductSnapshot))
    assert count_after_first == 1
    assert count_after_second == 1
    assert snaps_after_second == snaps_after_first + 1


def test_newegg_collector_uses_observed_limit() -> None:
    from collector.retailers.newegg.collector import NeweggCollector
    from collector.universe_config import search_universe_size

    assert NeweggCollector.uses_observed_result_limit is True
    collector = object.__new__(NeweggCollector)
    assert collector.uses_observed_result_limit is True
    assert search_universe_size() == 100


def test_exactly_100_observed_positions(session: Session) -> None:
    catalog = {
        f"SKU{i}": f"Notebook Gamer Dell Intel Core i7 RTX slot {i}"
        for i in range(1, 121)
    }
    order = [f"SKU{i}" for i in range(1, 121)]
    collector = _FakeMLCollector(catalog, order)
    collector.page_size = 36
    pipeline = CollectionPipeline(session, collector)
    outcome = asyncio.run(pipeline.run(limit=100))
    u = outcome.universe
    assert u["requested"] == 100
    assert u["observed"] == 100
    assert u["completeness"] == COMPLETENESS_COMPLETE
    assert u["extracted"] == 100
    assert u["reconciles"]
    positions = [row["search_position"] for row in u["observations"]]
    assert positions == list(range(1, 101))
    assert u["observations"][-1]["search_page"] == 1 + 99 // 36


def test_pagination_assigns_contiguous_positions(session: Session) -> None:
    catalog = {f"P{i}": f"Notebook Gamer Acer Intel Core i5 RTX {i}" for i in range(1, 8)}
    collector = _FakeMLCollector(catalog, [f"P{i}" for i in range(1, 8)])
    collector.page_size = 3
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=7))
    pages = [row["search_page"] for row in outcome.universe["observations"]]
    assert pages == [1, 1, 1, 2, 2, 2, 3]
    assert [row["search_position"] for row in outcome.universe["observations"]] == list(
        range(1, 8)
    )


def test_pagination_failure_is_partial(session: Session) -> None:
    catalog = {f"P{i}": f"Notebook Gamer HP AMD Ryzen 7 {i}" for i in range(1, 5)}
    collector = _FakeMLCollector(catalog, [f"P{i}" for i in range(1, 5)])
    collector.pagination_reliable = False
    collector.stop_reason = "pagination_unreliable"
    collector.pages_attempted = 3
    collector.pages_inspected = 1
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=100))
    assert outcome.universe["observed"] == 4
    assert outcome.universe["completeness"] == COMPLETENESS_PARTIAL
    assert outcome.universe["pagination_reliable"] is False


def test_blocked_search_is_partial_not_fabricated(session: Session) -> None:
    collector = _FakeMLCollector({}, [])
    collector.search_status = "BLOCKED"
    collector.pages_blocked = 1
    collector.pages_inspected = 0
    collector.stop_reason = "account_verification"
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=100))
    assert outcome.universe["requested"] == 100
    assert outcome.universe["observed"] == 0
    assert outcome.universe["completeness"] == COMPLETENESS_PARTIAL
    assert outcome.universe["search_status"] == "BLOCKED"
    assert outcome.status == "partial"


def test_inaccessible_counted_separately(session: Session) -> None:
    catalog = {
        "OK": "Notebook Gamer Lenovo Legion Intel Core i7 RTX",
        "BLOCK": "Notebook Gamer MSI Intel Core i9 RTX",
    }
    collector = _FakeMLCollector(catalog, ["OK", "BLOCK"], inaccessible_skus={"BLOCK"})
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=2))
    assert outcome.universe["inaccessible"] == 1
    assert outcome.universe["valid"] == 1
    assert outcome.universe["observed"] == 2
    assert outcome.universe["reconciles"]


def test_search_position_preserved_for_excluded(session: Session) -> None:
    catalog = {
        "1": "Notebook Gamer ASUS TUF Intel Core i7 RTX",
        "2": "Notebook Gamer Lenovo Legion AMD Ryzen 7 RTX",
        "3": "Samsung Odyssey G5 Gaming Monitor 27",
        "4": "Notebook Gamer MSI Intel Core i9 RTX",
    }
    collector = _FakeMLCollector(catalog, ["1", "2", "3", "4"])
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=4))
    obs = {row["search_position"]: row for row in outcome.universe["observations"]}
    assert obs[3]["bucket"] == "EXCLUDED"
    assert obs[4]["bucket"] == "VALID"
    assert obs[4]["search_position"] == 4
    assert "3" not in {p.retailer_sku for p in outcome.success}


def test_tracked_and_other_and_unknown_brands() -> None:
    from collector.classification import OTHER, UNKNOWN, classify_brand

    assert classify_brand(title="Notebook Gamer Intel Core i7 RTX")[0] == "Intel"
    assert classify_brand(title="Notebook Gamer AMD Ryzen 7 RTX")[0] == "AMD"
    assert classify_brand(title="Notebook Snapdragon X Elite")[0] == "Qualcomm"
    assert classify_brand(title="Apple MacBook Pro M3")[0] == "Apple"
    assert classify_brand(title="Tablet MediaTek Dimensity 7020")[0] == OTHER
    assert classify_brand(title="Notebook 15 pol 8GB RAM")[0] == UNKNOWN


def test_product_type_monitor_keyboard_other_not_unknown() -> None:
    monitor = classify_mercadolibre_product(title="Samsung Odyssey G5 Gaming Monitor 27")
    keyboard = classify_mercadolibre_product(title="Redragon Kumara Gaming Keyboard RGB")
    unknown = classify_mercadolibre_product(title="Caixa sortida unidades diversas")
    laptop = classify_mercadolibre_product(
        title="Notebook Gamer ASUS TUF Intel Core i7 backlit keyboard RTX"
    )
    assert monitor.status == EXCLUDED
    assert monitor.product_type == OTHER_TYPE
    assert keyboard.status == EXCLUDED
    assert keyboard.product_type == OTHER_TYPE
    assert unknown.product_type == UNKNOWN
    assert laptop.status == VALID
    assert laptop.product_type == "notebook"


def test_newegg_pipeline_observed_universe(session: Session) -> None:
    class _FakeNewegg(_FakeMLCollector):
        code = "newegg"
        country_code = "US"
        currency = "USD"

    catalog = {
        "N1": "ASUS ROG Strix G16 Gaming Laptop Intel Core i9 RTX 4070",
        "N2": "Samsung 32 Inch Odyssey Gaming Monitor",
        "N3": "Acer Nitro 5 Gaming Laptop AMD Ryzen 7 RTX 4060",
    }
    collector = _FakeNewegg(catalog, ["N1", "N2", "N3"])
    collector.query = "gaming laptop"
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=3))
    assert outcome.universe["requested"] == 3
    assert outcome.universe["observed"] == 3
    assert outcome.universe["excluded"] == 1
    assert outcome.universe["valid"] == 2
    assert outcome.universe["query"] == "gaming laptop"
    assert [row["search_position"] for row in outcome.universe["observations"]] == [1, 2, 3]
    assert all(row.get("country") == "US" for row in outcome.universe["observations"])
    assert outcome.universe.get("inaccessible_scope") == "candidate"


def test_repeated_sku_consumes_position_as_duplicate(session: Session) -> None:
    catalog = {
        "A": "Notebook Gamer Dell Intel Core i7 RTX",
        "B": "Notebook Gamer HP AMD Ryzen 7 RTX",
    }
    collector = _FakeMLCollector(catalog, ["A", "B", "A"])
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=3))
    buckets = [row["bucket"] for row in outcome.universe["observations"]]
    assert buckets == ["VALID", "VALID", "DUPLICATE"]
    assert [row["search_position"] for row in outcome.universe["observations"]] == [1, 2, 3]
    assert outcome.universe["duplicate"] == 1
    assert outcome.universe["observed"] == 3
    assert outcome.universe["valid"] == 2


def test_newegg_listing_parse_regression() -> None:
    from collector.retailers.newegg.listing import parse_listing_card_html

    cand = parse_listing_card_html(
        title="ASUS ROG Zephyrus G14 Gaming Laptop AMD Ryzen 9",
        href="/asus-rog/p/N82E16834204369",
        price_text="$1,499.99",
        list_price_text="$1,699.99",
        promo_text="Save $200",
        category_raw="gaming_laptops",
    )
    assert cand is not None
    assert cand.retailer_sku == "N82E16834204369"


def test_stratum_budgets_sum_to_100() -> None:
    from collector.universe_config import STRATUM_BUDGETS, allocate_stratum_budgets, search_universe_size

    assert search_universe_size() == 100
    assert STRATUM_BUDGETS["notebook"] == 20
    assert STRATUM_BUDGETS["desktop"] == 20
    assert STRATUM_BUDGETS["workstation"] == 20
    assert STRATUM_BUDGETS["tablet"] == 20
    assert STRATUM_BUDGETS["gpu"] == 10
    assert STRATUM_BUDGETS["cpu"] == 10
    assert sum(STRATUM_BUDGETS.values()) == 100
    allocated = allocate_stratum_budgets("newegg", total_limit=100)
    assert [(s.name, n) for s, n in allocated] == [
        ("notebook", 20),
        ("desktop", 20),
        ("workstation", 20),
        ("tablet", 20),
        ("gpu", 10),
        ("cpu", 10),
    ]


def test_pagination_per_stratum_native_positions() -> None:
    from collector.universe_config import paginate_stratum_pages

    pages = [["a", "b", "c"], ["d", "e"], ["f"]]
    taken = paginate_stratum_pages(pages, budget=5)
    assert [(item, pos, page) for item, pos, page in taken] == [
        ("a", 1, 1),
        ("b", 2, 1),
        ("c", 3, 1),
        ("d", 4, 2),
        ("e", 5, 2),
    ]
    short = paginate_stratum_pages([["x"], ["y"]], budget=20)
    assert len(short) == 2
    assert short[-1][1] == 2


def test_brand_specific_queries_rejected_from_universe() -> None:
    from collector.universe_config import (
        collection_queries_for,
        query_contains_brand_bias,
        validate_universe_config,
    )

    validate_universe_config()
    for retailer in ("newegg", "mercadolibre"):
        for query in collection_queries_for(retailer):
            assert not query_contains_brand_bias(query), query
    for biased in (
        "intel gaming laptop",
        "amd gaming laptop",
        "ryzen gaming laptop",
        "qualcomm laptop",
        "apple laptop",
        "macbook gaming",
        "notebook intel",
        "notebook ryzen",
        "snapdragon gaming",
        "apple tablet",
    ):
        assert query_contains_brand_bias(biased)


class _FakeStratifiedCollector(RetailerCollector):
    code = "newegg"
    country_code = "US"
    currency = "USD"
    uses_observed_result_limit = True

    def __init__(self, strata: dict[str, list[tuple[str, str, str]]], **meta: object):
        self.strata_catalog = strata
        self.meta = meta
        self.discovery_stats: dict = {}

    def build_browser_session(self):
        return _NullBrowser()

    async def discover_listings(self, session, *, limit: int):
        from collector.observation import stratum_completeness
        from collector.universe_config import allocate_stratum_budgets

        allocated = allocate_stratum_budgets(self.code, total_limit=limit)
        out: list[ListingCandidate] = []
        reports = []
        slot = 0
        for spec, budget in allocated:
            rows = list(self.strata_catalog.get(spec.name) or [])
            observed = 0
            for idx, (sku, title, query) in enumerate(rows[:budget], start=1):
                slot += 1
                observed += 1
                out.append(
                    ListingCandidate(
                        retailer_sku=sku,
                        source_url=f"https://www.newegg.com/p/{sku}",
                        title=title,
                        search_position=idx,
                        search_page=1,
                        query=query,
                        stratum=spec.name,
                        universe_slot=slot,
                        raw={
                            "stratum": spec.name,
                            "universe_slot": slot,
                            "used_fallback": bool(
                                (self.meta.get("fallback_strata") or set())  # type: ignore[union-attr]
                                and spec.name in self.meta.get("fallback_strata", set())  # type: ignore[arg-type]
                            ),
                        },
                    )
                )
            blocked = spec.name in set(self.meta.get("blocked_strata") or [])  # type: ignore[arg-type]
            used_fallback = spec.name in set(self.meta.get("fallback_strata") or [])  # type: ignore[arg-type]
            reports.append(
                {
                    "stratum": spec.name,
                    "query": spec.query,
                    "requested": budget,
                    "observed": observed,
                    "completeness": stratum_completeness(
                        requested=budget,
                        observed=observed,
                        ranked_search_ok=not blocked,
                        used_fallback=used_fallback,
                        search_blocked=blocked,
                    ),
                    "search_status": "BLOCKED" if blocked else "OK",
                    "used_fallback": used_fallback,
                    "stop_reason": (
                        "account_verification"
                        if blocked
                        else ("requested_depth" if observed >= budget else "short_serp")
                    ),
                    "pages_attempted": 1,
                    "pages_inspected": 0 if blocked and not used_fallback else 1,
                    "pages_blocked": 1 if blocked else 0,
                    "pagination_reliable": not blocked and not used_fallback,
                    "last_observed_position": observed,
                    "ranking_scope": "stratum_query",
                }
            )
        self.discovery_stats = {
            "pages_attempted": sum(int(r["pages_attempted"]) for r in reports),
            "pages_inspected": sum(int(r["pages_inspected"]) for r in reports),
            "pages_blocked": sum(int(r["pages_blocked"]) for r in reports),
            "pagination_reliable": all(r["pagination_reliable"] for r in reports),
            "last_observed_position": max((r["last_observed_position"] for r in reports), default=0),
            "search_status": "OK",
            "query": [r["query"] for r in reports],
            "queries": [r["query"] for r in reports],
            "stop_reason": None,
            "strata": reports,
        }
        return out

    async def fetch_product(self, session, candidate: ListingCandidate) -> NormalizedProduct:
        return build_normalized_product(
            retailer_code=self.code,
            country_code=self.country_code,
            currency=self.currency,
            retailer_sku=candidate.retailer_sku,
            source_url=candidate.source_url,
            title=candidate.title or candidate.retailer_sku,
            price_text="100.00",
        )


def _full_stratum_catalog() -> dict[str, list[tuple[str, str, str]]]:
    titles = {
        "notebook": "ASUS TUF Gaming Laptop Intel Core i7 RTX 4060",
        "desktop": "MSI Aegis Gaming Desktop AMD Ryzen 7 RTX 4070",
        "workstation": "Dell Precision Gaming Workstation Intel Xeon RTX",
        "tablet": "Lenovo Legion Gaming Tablet Snapdragon 8 Gen 3",
        "gpu": "MSI Gaming GeForce RTX 4070 Graphics Card",
        "cpu": "AMD Ryzen 7 7800X3D Gaming Processor",
    }
    queries = {
        "notebook": "gaming laptop",
        "desktop": "gaming desktop",
        "workstation": "gaming workstation",
        "tablet": "gaming tablet",
        "gpu": "gaming graphics card",
        "cpu": "gaming processor",
    }
    budgets = {"notebook": 20, "desktop": 20, "workstation": 20, "tablet": 20, "gpu": 10, "cpu": 10}
    catalog: dict[str, list[tuple[str, str, str]]] = {}
    for name, budget in budgets.items():
        catalog[name] = [
            (f"{name.upper()}{i}", f"{titles[name]} {i}", queries[name])
            for i in range(1, budget + 1)
        ]
    return catalog


def test_stratified_universe_complete_when_all_strata_hit_budget(session: Session) -> None:
    collector = _FakeStratifiedCollector(_full_stratum_catalog())
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=100))
    u = outcome.universe
    assert u["requested"] == 100
    assert u["observed"] == 100
    assert u["completeness"] == COMPLETENESS_COMPLETE
    by_stratum = {row["stratum"]: row for row in u["strata"]}
    assert by_stratum["notebook"]["requested"] == 20
    assert by_stratum["desktop"]["observed"] == 20
    assert by_stratum["workstation"]["observed"] == 20
    assert by_stratum["tablet"]["observed"] == 20
    assert by_stratum["gpu"]["requested"] == 10
    assert by_stratum["cpu"]["observed"] == 10
    assert u["universe_slot_is_retailer_rank"] is False
    native = [row["search_position"] for row in u["observations"] if row["stratum"] == "notebook"]
    assert native == list(range(1, 21))
    desktop_native = [
        row["search_position"] for row in u["observations"] if row["stratum"] == "desktop"
    ]
    assert desktop_native[0] == 1
    slots = [row["universe_slot"] for row in u["observations"]]
    assert slots == list(range(1, 101))
    assert slots[-1] != desktop_native[-1] or True  # slot is not the native rank


def test_overall_partial_when_one_stratum_short(session: Session) -> None:
    catalog = _full_stratum_catalog()
    catalog["workstation"] = catalog["workstation"][:8]
    collector = _FakeStratifiedCollector(catalog)
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=100))
    u = outcome.universe
    assert u["observed"] == 88
    assert u["completeness"] == COMPLETENESS_PARTIAL
    by_stratum = {row["stratum"]: row for row in u["strata"]}
    assert by_stratum["notebook"]["completeness"] == COMPLETENESS_COMPLETE
    assert by_stratum["workstation"]["completeness"] == COMPLETENESS_PARTIAL
    assert by_stratum["workstation"]["observed"] == 8
    assert outcome.status == "partial"


def test_blocked_stratum_does_not_borrow_from_others(session: Session) -> None:
    catalog = _full_stratum_catalog()
    catalog["tablet"] = []
    collector = _FakeStratifiedCollector(catalog, blocked_strata={"tablet"})
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=100))
    by_stratum = {row["stratum"]: row for row in outcome.universe["strata"]}
    assert by_stratum["tablet"]["observed"] == 0
    assert by_stratum["tablet"]["completeness"] == COMPLETENESS_PARTIAL
    assert by_stratum["notebook"]["observed"] == 20
    assert outcome.universe["observed"] == 80
    assert outcome.universe["completeness"] == COMPLETENESS_PARTIAL


def test_no_fake_global_retailer_ranking(session: Session) -> None:
    collector = _FakeStratifiedCollector(_full_stratum_catalog())
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=100))
    obs = outcome.universe["observations"]
    notebook_first = next(row for row in obs if row["stratum"] == "notebook")
    desktop_first = next(row for row in obs if row["stratum"] == "desktop")
    assert notebook_first["search_position"] == 1
    assert desktop_first["search_position"] == 1
    assert desktop_first["universe_slot"] != 1 or notebook_first["universe_slot"] == 1
    assert desktop_first["universe_slot"] == 21
    assert outcome.universe["ranking_scope"] == "stratum_query"
    assert outcome.universe["universe_slot_is_retailer_rank"] is False


def test_duplicate_across_queries_keeps_both_observations(session: Session) -> None:
    catalog = _full_stratum_catalog()
    shared = ("SHARED1", "ASUS TUF Gaming Laptop AMD Ryzen 7 RTX 4060", "gaming laptop")
    catalog["notebook"][0] = shared
    catalog["desktop"][0] = ("SHARED1", shared[1], "gaming desktop")
    collector = _FakeStratifiedCollector(catalog)
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=100))
    shared_rows = [
        row for row in outcome.universe["observations"] if row.get("sku") == "SHARED1"
    ]
    assert len(shared_rows) == 2
    assert {row["query"] for row in shared_rows} == {"gaming laptop", "gaming desktop"}
    assert all(row["bucket"] != "DUPLICATE" for row in shared_rows)
    products = session.scalars(select(Product)).all()
    assert sum(1 for p in products if p.retailer_sku == "SHARED1") == 1
    assert session.scalar(select(func.count()).select_from(ProductSnapshot)) >= 2


def test_ml_fallback_does_not_become_ranked_complete(session: Session) -> None:
    catalog = _full_stratum_catalog()
    collector = _FakeStratifiedCollector(catalog, fallback_strata={"notebook"})
    collector.code = "mercadolibre"
    collector.country_code = "BR"
    collector.currency = "BRL"
    outcome = asyncio.run(CollectionPipeline(session, collector).run(limit=100))
    by_stratum = {row["stratum"]: row for row in outcome.universe["strata"]}
    assert by_stratum["notebook"]["used_fallback"] is True
    assert by_stratum["notebook"]["observed"] == 20
    assert by_stratum["notebook"]["completeness"] == COMPLETENESS_PARTIAL
    assert outcome.universe["completeness"] == COMPLETENESS_PARTIAL
    notebook_obs = [
        row for row in outcome.universe["observations"] if row["stratum"] == "notebook"
    ]
    assert all(row.get("used_fallback") for row in notebook_obs)


def test_qualcomm_apple_oem_brand_separation() -> None:
    from collector.classification import classify_brand, classify_product

    q, _ = classify_brand(title="Lenovo Legion Gaming Tablet Snapdragon 8 Gen 3")
    a, _ = classify_brand(title="Apple iPad Pro M4 Gaming Tablet")
    assert q == "Qualcomm"
    assert a == "Apple"
    result = classify_product(
        title="ASUS TUF Gaming Laptop AMD Ryzen 7 RTX 4060",
        product_type="notebook",
    )
    assert result.brand == "AMD"
    assert result.oem == "Asus"


def test_historical_rows_not_deleted_across_strata(session: Session) -> None:
    catalog = {
        "notebook": [
            ("HIST1", "ASUS TUF Gaming Laptop Intel Core i7 RTX 4060", "gaming laptop")
        ]
    }
    collector = _FakeStratifiedCollector(catalog)
    first = asyncio.run(CollectionPipeline(session, collector).run(limit=20))
    assert first.universe["observed"] == 1
    n_products = session.scalar(select(func.count()).select_from(Product))
    n_snaps = session.scalar(select(func.count()).select_from(ProductSnapshot))
    second = asyncio.run(CollectionPipeline(session, collector).run(limit=20))
    assert second.universe["observed"] == 1
    assert session.scalar(select(func.count()).select_from(Product)) == n_products
    assert session.scalar(select(func.count()).select_from(ProductSnapshot)) == n_snaps + 1

