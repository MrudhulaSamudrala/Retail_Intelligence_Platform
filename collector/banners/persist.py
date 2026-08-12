"""Append-only persistence for homepage banner observations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.orm import Session

from collector.banners.detect import DetectedBanner
from database.models import BannerObservation
from database.repositories import CollectionRunRepository, ObservationRepository


def persist_banners(
    session: Session,
    banners: Sequence[DetectedBanner],
    *,
    retailer_code: str,
    country_code: str,
    collection_run_id: int | None = None,
    observed_at: datetime | None = None,
    page_type: str = "homepage",
) -> list[BannerObservation]:
    """Append banner rows. Never updates prior observations."""
    observed = observed_at or datetime.now(timezone.utc)
    obs = ObservationRepository(session)
    rows: list[BannerObservation] = []
    for banner in banners:
        row = obs.add_banner(
            collection_run_id=collection_run_id,
            observed_at=observed,
            retailer_code=retailer_code,
            country_code=country_code,
            page_type=page_type,
            page_url=banner.source_url,
            banner_position=banner.banner_position,
            brand_detected=banner.brand,
            oem_detected=None,
            headline_text=banner.banner_text,
            discount_text=banner.discount_text,
            badge_text=banner.badge_text,
            link_present=bool(banner.link_present),
            destination_url=banner.link_url,
            is_tracked_brand=bool(banner.is_tracked_brand),
            evidence_text=banner.evidence_text,
            selector=banner.selector,
            detection_method=banner.detection_method,
            screenshot_path=banner.screenshot_path,
            details=banner.details or None,
        )
        rows.append(row)
    session.flush()
    return rows


def start_banner_run(
    session: Session,
    *,
    retailer_code: str,
    country_code: str,
) -> int:
    run = CollectionRunRepository(session).start(
        retailer_code=retailer_code,
        country_code=country_code,
        run_type="banner",
        run_metadata={"source": "collector.banners", "page_type": "homepage"},
    )
    session.flush()
    return int(run.id)
