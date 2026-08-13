"""Conservative cross-retailer product matching.

Never merges retailer ``products`` rows. Writes ``canonical_products`` +
``product_crosswalk`` only.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from analytics.product_identity.config import (
    ProductIdentityConfig,
    load_product_identity_config,
)
from database.models import CanonicalProduct, Product, ProductCrosswalk, ProductSnapshot

logger = logging.getLogger("analytics.product_identity.matching")

MATCHED = "MATCHED"
POSSIBLE_MATCH = "POSSIBLE_MATCH"
UNMATCHED = "UNMATCHED"

# Manufacturer model / MPN-like tokens (conservative).
_MODEL_TOKEN_RE = re.compile(
    r"\b("
    r"[A-Z]{1,4}\d{2,}[A-Z0-9\-]{2,}|"  # M1502YA-NJ611, A515-45-R2A3
    r"[A-Z]{2,}\-\d{2,}[A-Z0-9\-]*|"  # FE16-..., X1504VA
    r"\d{2}[A-Z]{2}\d{4}[A-Z0-9]*|"  # 83NS0002BR
    r"[A-Z]{3,}\d{2,}[A-Z0-9]{2,}"  # VJFE69F11X
    r")\b",
    re.IGNORECASE,
)

_NOISE_TOKENS = frozenset(
    {
        "notebook",
        "laptop",
        "gaming",
        "gamer",
        "windows",
        "linux",
        "home",
        "ssd",
        "hdd",
        "ram",
        "gb",
        "tb",
        "fhd",
        "qhd",
        "oled",
        "ips",
        "wifi",
        "bluetooth",
        "geforce",
        "radeon",
        "intel",
        "amd",
        "ryzen",
        "core",
        "ultra",
        "snapdragon",
        "apple",
        "com",
        "the",
        "and",
        "for",
        "with",
        "tela",
        "memoria",
        "memória",
        "armazenamento",
    }
)


@dataclass
class ProductFingerprint:
    product_id: int
    retailer_code: str
    country_code: str
    retailer_sku: str
    title: Optional[str]
    brand: Optional[str]
    oem: Optional[str]
    product_type: Optional[str]
    manufacturer_model: Optional[str] = None
    model_tokens: tuple[str, ...] = ()
    processor: Optional[str] = None
    gpu: Optional[str] = None
    ram: Optional[str] = None
    storage: Optional[str] = None
    normalized_title: str = ""
    title_tokens: frozenset[str] = field(default_factory=frozenset)


@dataclass
class PairScore:
    confidence: float
    method: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s\-_/]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_manufacturer_model(title: Optional[str], raw_payload: Optional[dict] = None) -> Optional[str]:
    """Extract strongest manufacturer model / MPN candidate."""
    candidates: list[str] = []
    if isinstance(raw_payload, dict):
        for key in (
            "manufacturer_model",
            "mpn",
            "model",
            "model_number",
            "item_number",
        ):
            val = raw_payload.get(key)
            if isinstance(val, str) and len(val.strip()) >= 4:
                candidates.append(val.strip().upper())
        specs = raw_payload.get("specs")
        if isinstance(specs, dict):
            for key, val in specs.items():
                if not isinstance(val, str):
                    continue
                if any(t in str(key).lower() for t in ("model", "mpn", "part")):
                    if len(val.strip()) >= 4:
                        candidates.append(val.strip().upper())

    if title:
        # Prefer token after final " - " (common on Mercado Libre titles).
        if " - " in title:
            tail = title.rsplit(" - ", 1)[-1].strip()
            if 4 <= len(tail) <= 40 and re.search(r"[A-Za-z].*\d|\d.*[A-Za-z]", tail):
                candidates.append(tail.upper())
        for match in _MODEL_TOKEN_RE.finditer(title):
            token = match.group(1).upper()
            if token not in _NOISE_TOKENS and len(token) >= 5:
                candidates.append(token)

    # Prefer longest distinct token (more specific MPN).
    uniq = sorted(set(candidates), key=lambda x: (-len(x), x))
    return uniq[0] if uniq else None


def _spec_from_payload(raw: Optional[dict], *keys: str) -> Optional[str]:
    if not isinstance(raw, dict):
        return None
    for key in keys:
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    specs = raw.get("specs")
    if isinstance(specs, dict):
        lowered = {str(k).lower(): v for k, v in specs.items() if isinstance(v, str)}
        for key in keys:
            for lk, lv in lowered.items():
                if key.lower() in lk and lv.strip():
                    return lv.strip()
    return None


def _title_tokens(title: Optional[str]) -> frozenset[str]:
    norm = normalize_text(title)
    toks = {
        t
        for t in re.split(r"[\s\-_/]+", norm)
        if len(t) >= 3 and t not in _NOISE_TOKENS and not t.isdigit()
    }
    return frozenset(toks)


def build_fingerprint(product: Product, raw_payload: Optional[dict] = None) -> ProductFingerprint:
    raw = raw_payload if isinstance(raw_payload, dict) else {}
    model = extract_manufacturer_model(product.title, raw)
    model_tokens = tuple(
        sorted(
            {
                m.group(1).upper()
                for m in _MODEL_TOKEN_RE.finditer(product.title or "")
                if len(m.group(1)) >= 5
            }
        )
    )
    return ProductFingerprint(
        product_id=int(product.id),
        retailer_code=product.retailer_code,
        country_code=product.country_code,
        retailer_sku=product.retailer_sku,
        title=product.title,
        brand=product.brand,
        oem=None if (product.oem or "").upper() == "UNKNOWN" else product.oem,
        product_type=product.product_type,
        manufacturer_model=model,
        model_tokens=model_tokens,
        processor=_spec_from_payload(raw, "processor", "cpu", "Processador")
        or _cpu_from_title(product.title),
        gpu=_spec_from_payload(raw, "gpu", "Placa de vídeo", "graphics"),
        ram=_spec_from_payload(raw, "ram", "Memória RAM", "memory"),
        storage=_spec_from_payload(raw, "storage", "Armazenamento"),
        normalized_title=normalize_text(product.title),
        title_tokens=_title_tokens(product.title),
    )


def _cpu_from_title(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    m = re.search(
        r"(intel\s+core\s*(?:ultra\s*)?(?:i?\d[^,;/]*)|"
        r"amd\s+ryzen\s*(?:ai\s*)?\d[^,;/]*|"
        r"snapdragon[^,;/]*|"
        r"apple\s+m\d[^,;/]*)",
        title,
        re.I,
    )
    return m.group(1).strip() if m else None


def _norm_spec(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _oem_equal(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    return a.strip().lower() == b.strip().lower()


def _model_equal(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    na = re.sub(r"[^A-Z0-9]", "", a.upper())
    nb = re.sub(r"[^A-Z0-9]", "", b.upper())
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _title_similarity(a: ProductFingerprint, b: ProductFingerprint) -> float:
    if not a.normalized_title or not b.normalized_title:
        return 0.0
    seq = SequenceMatcher(None, a.normalized_title, b.normalized_title).ratio()
    if a.title_tokens and b.title_tokens:
        inter = len(a.title_tokens & b.title_tokens)
        union = len(a.title_tokens | b.title_tokens)
        jaccard = inter / union if union else 0.0
        return max(seq, jaccard)
    return seq


def score_pair(
    left: ProductFingerprint,
    right: ProductFingerprint,
    *,
    config: ProductIdentityConfig | None = None,
) -> PairScore:
    """Score whether two retailer products are the same real-world product."""
    cfg = config or load_product_identity_config()
    if left.retailer_code == right.retailer_code:
        return PairScore(0.0, "same_retailer", UNMATCHED)

    details: dict[str, Any] = {}
    title_sim = _title_similarity(left, right)
    details["title_similarity"] = round(title_sim, 4)

    oem_ok = _oem_equal(left.oem, right.oem)
    details["oem_match"] = oem_ok

    model_ok = _model_equal(left.manufacturer_model, right.manufacturer_model)
    if not model_ok and left.model_tokens and right.model_tokens:
        model_ok = bool(set(left.model_tokens) & set(right.model_tokens))
    details["model_match"] = model_ok
    details["left_model"] = left.manufacturer_model
    details["right_model"] = right.manufacturer_model

    cpu_ok = bool(
        left.processor
        and right.processor
        and (
            _norm_spec(left.processor) in _norm_spec(right.processor)
            or _norm_spec(right.processor) in _norm_spec(left.processor)
        )
    )
    ram_ok = bool(
        left.ram and right.ram and _norm_spec(left.ram) == _norm_spec(right.ram)
    )
    storage_ok = bool(
        left.storage
        and right.storage
        and _norm_spec(left.storage) == _norm_spec(right.storage)
    )
    details["cpu_match"] = cpu_ok
    details["ram_match"] = ram_ok
    details["storage_match"] = storage_ok

    # 1) Exact manufacturer model + OEM → MATCHED
    if model_ok and (oem_ok or not cfg.require_oem_for_matched):
        conf = 0.95 if oem_ok else 0.88
        if cpu_ok:
            conf = min(0.99, conf + 0.02)
        return PairScore(conf, "manufacturer_model", MATCHED, details)

    # 2) OEM + model token overlap already handled above
    # 3) OEM + strong title + CPU/RAM/storage support → MATCHED or POSSIBLE
    if oem_ok and title_sim >= 0.72 and (cpu_ok or (ram_ok and storage_ok)):
        conf = 0.84 if cpu_ok and ram_ok else 0.80
        return PairScore(conf, "oem_model_specs", MATCHED, details)

    if oem_ok and title_sim >= 0.65 and cpu_ok:
        return PairScore(0.72, "oem_cpu_title", POSSIBLE_MATCH, details)

    if oem_ok and title_sim >= 0.60:
        conf = min(cfg.title_similarity_cap + 0.15, 0.62)
        return PairScore(conf, "oem_title", POSSIBLE_MATCH, details)

    # Title-only never MATCHED
    if title_sim >= 0.85:
        conf = min(title_sim * 0.5, cfg.title_similarity_cap)
        return PairScore(conf, "title_only", POSSIBLE_MATCH, details)

    return PairScore(0.0, "no_signal", UNMATCHED, details)


def _latest_raw_payload(session: Session, product_id: int) -> Optional[dict]:
    snap = session.scalars(
        select(ProductSnapshot)
        .where(ProductSnapshot.product_id == product_id)
        .order_by(ProductSnapshot.observed_at.desc())
        .limit(1)
    ).first()
    if snap is None or not isinstance(snap.raw_payload, dict):
        return None
    return snap.raw_payload


def rebuild_cross_retailer_identity(
    session: Session,
    *,
    config: ProductIdentityConfig | None = None,
    clear_existing: bool = True,
) -> dict[str, Any]:
    """Rebuild canonical products + crosswalk from current ``products`` rows.

    Retailer product rows are never modified.
    """
    cfg = config or load_product_identity_config()
    products = list(
        session.scalars(
            select(Product).where(Product.is_active.is_(True)).order_by(Product.id.asc())
        ).all()
    )
    fingerprints = [
        build_fingerprint(p, _latest_raw_payload(session, int(p.id))) for p in products
    ]
    by_retailer: dict[str, list[ProductFingerprint]] = {}
    for fp in fingerprints:
        by_retailer.setdefault(fp.retailer_code, []).append(fp)

    lefts = by_retailer.get("newegg", [])
    rights = by_retailer.get("mercadolibre", [])

    # Greedy best unique pairing across retailers
    candidates: list[tuple[float, ProductFingerprint, ProductFingerprint, PairScore]] = []
    for left in lefts:
        for right in rights:
            scored = score_pair(left, right, config=cfg)
            if scored.status in (MATCHED, POSSIBLE_MATCH) and scored.confidence > 0:
                candidates.append((scored.confidence, left, right, scored))
    candidates.sort(key=lambda x: (-x[0], x[1].product_id, x[2].product_id))

    used_left: set[int] = set()
    used_right: set[int] = set()
    pairs: list[tuple[ProductFingerprint, ProductFingerprint, PairScore]] = []
    for conf, left, right, scored in candidates:
        if left.product_id in used_left or right.product_id in used_right:
            continue
        if scored.status == POSSIBLE_MATCH and conf < cfg.possible_min:
            continue
        used_left.add(left.product_id)
        used_right.add(right.product_id)
        pairs.append((left, right, scored))

    if clear_existing:
        session.execute(delete(ProductCrosswalk))
        session.execute(delete(CanonicalProduct))
        session.flush()

    summary = {
        "matched_pairs": 0,
        "possible_pairs": 0,
        "unmatched_products": 0,
        "canonical_products": 0,
        "crosswalk_rows": 0,
    }
    assigned: set[int] = set()

    for left, right, scored in pairs:
        status = scored.status
        if status == MATCHED and scored.confidence < cfg.matched_min:
            status = POSSIBLE_MATCH
        if status == POSSIBLE_MATCH and scored.confidence < cfg.possible_min:
            status = UNMATCHED
            continue
        if status == UNMATCHED:
            continue

        canon = CanonicalProduct(
            brand=left.brand or right.brand,
            oem=left.oem or right.oem,
            model_name=(left.title or right.title or "")[:256] or None,
            manufacturer_model=left.manufacturer_model or right.manufacturer_model,
            normalized_name=left.normalized_title or right.normalized_title,
            product_type=left.product_type or right.product_type,
            details={
                "pair": [left.product_id, right.product_id],
                "score": scored.details,
            },
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(canon)
        session.flush()
        summary["canonical_products"] += 1

        for fp in (left, right):
            session.add(
                ProductCrosswalk(
                    canonical_product_id=canon.id,
                    product_id=fp.product_id,
                    match_status=status,
                    match_method=scored.method,
                    match_confidence=Decimal(str(round(scored.confidence, 4))),
                    details={"paired_with": left.product_id if fp is right else right.product_id},
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
            )
            assigned.add(fp.product_id)
            summary["crosswalk_rows"] += 1

        if status == MATCHED:
            summary["matched_pairs"] += 1
        else:
            summary["possible_pairs"] += 1

    # Singletons → own canonical identity, UNMATCHED
    for fp in fingerprints:
        if fp.product_id in assigned:
            continue
        canon = CanonicalProduct(
            brand=fp.brand,
            oem=fp.oem,
            model_name=(fp.title or "")[:256] or None,
            manufacturer_model=fp.manufacturer_model,
            normalized_name=fp.normalized_title,
            product_type=fp.product_type,
            details={"singleton_retailer": fp.retailer_code},
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(canon)
        session.flush()
        session.add(
            ProductCrosswalk(
                canonical_product_id=canon.id,
                product_id=fp.product_id,
                match_status=UNMATCHED,
                match_method="singleton",
                match_confidence=Decimal("0"),
                details=None,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
        )
        summary["canonical_products"] += 1
        summary["crosswalk_rows"] += 1
        summary["unmatched_products"] += 1

    session.flush()
    logger.info(
        "cross_retailer_identity_rebuilt",
        extra={"event": "cross_retailer_identity_rebuilt", **summary},
    )
    return summary
