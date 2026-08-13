"""Multi-layer Mercado Libre evidence extraction (pure + Playwright helpers).

Layer order:
1. Visible DOM
2. aria-label / title / alt
3. JSON-LD / structured data
4. Embedded application JSON/state
5. Legitimate network/API responses used by the page
6. Listing/card data
7. Title heuristics (last resort, not spec-table evidence)

Does not request private APIs independently and does not bypass access controls.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from collector.retailers.mercadolibre.field_evidence import (
    METHOD_DOM_ALT,
    METHOD_DOM_ARIA,
    METHOD_DOM_TEXT,
    METHOD_DOM_TITLE,
    METHOD_EMBEDDED_STATE,
    METHOD_JSON_LD,
    METHOD_LISTING_CARD,
    METHOD_NETWORK_JSON,
    METHOD_TITLE_REGEX,
    SOURCE_ARIA,
    SOURCE_EMBEDDED_JSON,
    SOURCE_JSON_LD,
    SOURCE_LISTING_CARD,
    SOURCE_NETWORK,
    SOURCE_PRODUCT_PAGE,
    SOURCE_TITLE_HEURISTIC,
    FieldObservation,
    ProvenanceStore,
    observation,
)
from collector.retailers.mercadolibre.listing import extract_mlb_id
from collector.retailers.mercadolibre.pt_labels import english_spec_key, normalize_spec_map

_JSON_ASSIGN_RE = re.compile(
    r"(?:window\.)?(?:__PRELOADED_STATE__|__INITIAL_STATE__|__NEXT_DATA__|"
    r"preloadedState)\s*=\s*",
    re.I,
)

_PRICE_KEYS = ("price", "amount", "value", "fraction")
_ATTR_NAME_KEYS = ("name", "id", "attribute_group_name")
_ATTR_VALUE_KEYS = ("value_name", "value", "values")


def _as_text(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).strip()
    return text or None


def _first_str(*values: Any) -> Optional[str]:
    for value in values:
        text = _as_text(value)
        if text:
            return text
    return None


def _offer_blob(offers: Any) -> dict[str, Any]:
    if isinstance(offers, list) and offers and isinstance(offers[0], dict):
        return offers[0]
    if isinstance(offers, dict):
        return offers
    return {}


def specs_from_title(title: Optional[str]) -> dict[str, str]:
    """Extract common notebook attributes embedded in ML titles (PT or EN)."""
    specs: dict[str, str] = {}
    if not title:
        return specs
    t = title

    m = re.search(
        r"(intel\s+core(?:\s+ultra)?(?:\s+i[3579](?:-\w+)?|\s+\d+)?|"
        r"amd\s+ryzen(?:\s+ai)?(?:\s+\d+)?(?:\s+[a-z]?\d{3,5}\w*)?|"
        r"snapdragon(?:\s+x)?(?:\s+(?:elite|plus))?(?:\s+\w+)?|"
        r"apple\s+m[1-4](?:\s*(?:pro|max|ultra))?)",
        t,
        re.I,
    )
    if m:
        specs["Processador"] = m.group(1).strip()

    m = re.search(
        r"(rtx\s*\d{3,4}\s*(?:ti|super)?|"
        r"gtx\s*\d{3,4}|"
        r"radeon\s*rx[^,;/]*|"
        r"intel\s+iris\s*xe|"
        r"geforce[^,;/]*)",
        t,
        re.I,
    )
    if m:
        specs["Placa de vídeo"] = m.group(1).strip()

    m = re.search(r"(\d+)\s*GB\s*(?:RAM|mem[oó]ria)?", t, re.I)
    if m:
        m2 = re.search(r"(\d+)\s*GB\s*RAM", t, re.I)
        specs["Memória RAM"] = f"{(m2 or m).group(1)} GB"

    m = re.search(r"(\d+)\s*GB\s*SSD|SSD\s*(\d+)\s*GB|(\d+)\s*TB\s*SSD", t, re.I)
    if m:
        gb = m.group(1) or m.group(2)
        tb = m.group(3)
        specs["Armazenamento"] = f"{tb} TB SSD" if tb else f"{gb} GB SSD"

    m = re.search(
        r"(windows\s*1[01](?:\s*home|\s*pro)?|chrome\s*os|macos|"
        r"keep\s*os|keepos|linux|"
        r"sem\s*sistema\s*operacional)",
        t,
        re.I,
    )
    if m:
        specs["Sistema operacional"] = m.group(1).strip()

    m = re.search(r'(\d{2}(?:[.,]\d)?)\s*(?:["”]|pol|polegadas|inch)', t, re.I)
    if m:
        specs["Tela"] = f'{m.group(1)}"'

    return specs


def apply_title_heuristics(store: ProvenanceStore, title: Optional[str]) -> None:
    store.mark_layer("title_heuristic")
    specs = specs_from_title(title)
    _ingest_specs(
        store,
        specs,
        source=SOURCE_TITLE_HEURISTIC,
        method=METHOD_TITLE_REGEX,
    )


def apply_listing_card(store: ProvenanceStore, card: dict[str, Any]) -> None:
    store.mark_layer("listing_card")
    title = _first_str(card.get("title"), card.get("aria_title"), card.get("link_title"))
    store.set_if_empty(
        "title",
        observation(title, source=SOURCE_LISTING_CARD, extraction_method=METHOD_LISTING_CARD)
        if title
        else None,
    )
    price = _first_str(card.get("price_text"), card.get("price_fraction"))
    if price:
        store.set_if_empty(
            "price",
            observation(
                price,
                source=SOURCE_LISTING_CARD,
                extraction_method=METHOD_LISTING_CARD,
                currency="BRL",
            ),
        )
    list_price = _first_str(card.get("list_price_text"), card.get("list_price"))
    if list_price:
        store.set_if_empty(
            "list_price",
            observation(
                list_price,
                source=SOURCE_LISTING_CARD,
                extraction_method=METHOD_LISTING_CARD,
                currency="BRL",
            ),
        )
    promo = _first_str(card.get("promo_text"), card.get("discount"))
    if promo:
        store.set_if_empty(
            "promo_text",
            observation(promo, source=SOURCE_LISTING_CARD, extraction_method=METHOD_LISTING_CARD),
        )
    sku = _first_str(card.get("sku")) or extract_mlb_id(
        str(card.get("href") or card.get("source_url") or ""), title or ""
    )
    if sku:
        store.set_if_empty(
            "retailer_sku",
            observation(sku, source=SOURCE_LISTING_CARD, extraction_method=METHOD_LISTING_CARD),
        )
    attrs = card.get("attributes") or {}
    if isinstance(attrs, dict):
        _ingest_specs(
            store,
            {str(k): str(v) for k, v in attrs.items() if v},
            source=SOURCE_LISTING_CARD,
            method=METHOD_LISTING_CARD,
        )
    elif isinstance(attrs, list):
        mapped: dict[str, str] = {}
        joined_parts: list[str] = []
        for item in attrs:
            if isinstance(item, str) and item.strip():
                mapped[f"listing_attr_{len(mapped)}"] = item.strip()
                joined_parts.append(item.strip())
            elif isinstance(item, dict):
                key = _first_str(item.get("name"), item.get("id")) or f"attr_{len(mapped)}"
                val = _first_str(item.get("value"), item.get("value_name"))
                if val:
                    mapped[key] = val
                    joined_parts.append(f"{key} {val}")
        _ingest_specs(store, mapped, source=SOURCE_LISTING_CARD, method=METHOD_LISTING_CARD)
        if joined_parts:
            _ingest_specs(
                store,
                specs_from_title(" ".join(joined_parts)),
                source=SOURCE_LISTING_CARD,
                method=METHOD_LISTING_CARD,
            )

    aria = _first_str(card.get("aria_label"), card.get("aria_title"))
    if aria:
        store.set_if_empty(
            "title",
            observation(aria, source=SOURCE_ARIA, extraction_method=METHOD_DOM_ARIA),
        )
    alt = _first_str(card.get("img_alt"))
    if alt and not store.get_value("title"):
        store.set_if_empty(
            "title",
            observation(alt, source=SOURCE_LISTING_CARD, extraction_method=METHOD_DOM_ALT),
        )


def apply_json_ld(store: ProvenanceStore, json_ld: dict[str, Any]) -> None:
    store.mark_layer("json_ld")
    if not json_ld:
        return
    title = _first_str(json_ld.get("name"))
    if title:
        store.set_if_empty(
            "title",
            observation(title, source=SOURCE_JSON_LD, extraction_method=METHOD_JSON_LD),
        )
    brand = json_ld.get("brand")
    brand_name = None
    if isinstance(brand, dict):
        brand_name = _first_str(brand.get("name"))
    else:
        brand_name = _first_str(brand)
    if brand_name:
        store.set_if_empty(
            "oem_raw",
            observation(brand_name, source=SOURCE_JSON_LD, extraction_method=METHOD_JSON_LD),
        )
    sku = _first_str(json_ld.get("sku"), json_ld.get("productID"), json_ld.get("mpn"))
    if sku:
        mlb = extract_mlb_id(str(sku), "") or sku
        store.set_if_empty(
            "retailer_sku",
            observation(mlb, source=SOURCE_JSON_LD, extraction_method=METHOD_JSON_LD),
        )
    mpn = _first_str(json_ld.get("mpn"), json_ld.get("model"))
    if mpn:
        store.set_if_empty(
            "mpn",
            observation(mpn, source=SOURCE_JSON_LD, extraction_method=METHOD_JSON_LD),
        )
    gtin = _first_str(
        json_ld.get("gtin13"),
        json_ld.get("gtin12"),
        json_ld.get("gtin14"),
        json_ld.get("gtin8"),
        json_ld.get("gtin"),
        json_ld.get("isbn"),
    )
    if gtin:
        store.set_if_empty(
            "gtin",
            observation(gtin, source=SOURCE_JSON_LD, extraction_method=METHOD_JSON_LD),
        )
    offers = _offer_blob(json_ld.get("offers"))
    price = _first_str(offers.get("price"), offers.get("lowPrice"))
    if price:
        currency = _first_str(offers.get("priceCurrency")) or "BRL"
        store.set_if_empty(
            "price",
            observation(
                price,
                source=SOURCE_JSON_LD,
                extraction_method=METHOD_JSON_LD,
                currency=currency,
            ),
        )
    availability = _first_str(offers.get("availability"))
    if availability:
        store.set_if_empty(
            "availability",
            observation(availability, source=SOURCE_JSON_LD, extraction_method=METHOD_JSON_LD),
        )
    extra_props = json_ld.get("additionalProperty") or json_ld.get("additionalProperties")
    specs: dict[str, str] = {}
    if isinstance(extra_props, list):
        for prop in extra_props:
            if not isinstance(prop, dict):
                continue
            key = _first_str(prop.get("name"), prop.get("propertyID"))
            val = _first_str(prop.get("value"))
            if key and val:
                specs[key] = val
    _ingest_specs(store, specs, source=SOURCE_JSON_LD, method=METHOD_JSON_LD)


def apply_embedded_or_network(
    store: ProvenanceStore,
    payload: Any,
    *,
    source: str,
    method: str,
    layer_name: str,
) -> None:
    store.mark_layer(layer_name)
    if payload is None:
        return
    productish = _find_productish_dicts(payload)
    for item in productish:
        _ingest_ml_item(store, item, source=source, method=method)


def parse_embedded_script_text(raw: str) -> Any:
    """Parse a script assignment or raw JSON blob; return None if not JSON."""
    text = (raw or "").strip()
    if not text:
        return None
    match = _JSON_ASSIGN_RE.search(text)
    if match:
        text = text[match.end() :].strip()
        if text.endswith(";"):
            text = text[:-1].strip()
    if text.startswith("JSON.parse("):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def parse_json_ld_products(raw_scripts: list[str]) -> dict[str, Any]:
    for raw in raw_scripts:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            nodes = graph if isinstance(graph, list) else [item]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                typ = node.get("@type")
                types = typ if isinstance(typ, list) else [typ]
                if "Product" in types:
                    return node
    return {}


def pick_spec(specs: dict[str, str], aliases: list[str]) -> Optional[str]:
    lowered = {k.lower().strip(): v.strip() for k, v in specs.items() if v}
    for alias in aliases:
        if alias in lowered and lowered[alias]:
            return lowered[alias]
    for key, value in lowered.items():
        for alias in aliases:
            if alias in key and value:
                return value
    normalized = normalize_spec_map(specs)
    for alias in aliases:
        en = english_spec_key(alias) or alias
        if en in normalized:
            return normalized[en]
    return None


def _ingest_specs(
    store: ProvenanceStore,
    specs: dict[str, str],
    *,
    source: str,
    method: str,
) -> None:
    if not specs:
        return
    existing = store.get_value("specs_raw") or {}
    if not isinstance(existing, dict):
        existing = {}
    merged = dict(existing)
    for key, value in specs.items():
        if key not in merged and value:
            merged[key] = value
    store.fields["specs_raw"] = observation(
        merged,
        source=source,
        extraction_method=method,
        confidence=store.fields.get("specs_raw").confidence
        if store.fields.get("specs_raw")
        else None,
    )
    normalized = normalize_spec_map(merged)
    for en_key, value in normalized.items():
        store.set_if_empty(
            en_key,
            observation(value, source=source, extraction_method=method),
        )


def _ingest_ml_item(
    store: ProvenanceStore,
    item: dict[str, Any],
    *,
    source: str,
    method: str,
) -> None:
    title = _first_str(item.get("title"), item.get("name"), item.get("permalink_title"))
    if title:
        store.set_if_empty(
            "title",
            observation(title, source=source, extraction_method=method),
        )
    price = _first_str(
        item.get("price"),
        (item.get("price") or {}).get("amount") if isinstance(item.get("price"), dict) else None,
        item.get("sale_price"),
    )
    if isinstance(item.get("price"), dict):
        price = price or _first_str(
            item["price"].get("amount"),
            item["price"].get("fraction"),
            item["price"].get("value"),
        )
    if price:
        currency = "BRL"
        if isinstance(item.get("price"), dict):
            currency = _first_str(item["price"].get("currency"), item["price"].get("currency_id")) or currency
        currency = _first_str(item.get("currency_id"), item.get("currency")) or currency
        store.set_if_empty(
            "price",
            observation(price, source=source, extraction_method=method, currency=currency),
        )
    original = _first_str(
        item.get("original_price"),
        item.get("base_price"),
        item.get("regular_price"),
    )
    if original:
        store.set_if_empty(
            "list_price",
            observation(original, source=source, extraction_method=method, currency="BRL"),
        )
    sku = _first_str(
        item.get("id"),
        item.get("item_id"),
        item.get("catalog_product_id"),
        item.get("product_id"),
    )
    if sku:
        mlb = extract_mlb_id(str(sku), title or "") or str(sku)
        store.set_if_empty(
            "retailer_sku",
            observation(mlb, source=source, extraction_method=method),
        )
    gtin = _first_str(item.get("gtin"), item.get("ean"), item.get("upc"))
    if gtin:
        store.set_if_empty(
            "gtin",
            observation(gtin, source=source, extraction_method=method),
        )
    mpn = _first_str(item.get("mpn"), item.get("model"), item.get("inventory_id"))
    if mpn:
        store.set_if_empty(
            "mpn",
            observation(mpn, source=source, extraction_method=method),
        )
    attrs = item.get("attributes") or item.get("specs") or item.get("specifications")
    mapped: dict[str, str] = {}
    if isinstance(attrs, list):
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            key = _first_str(*(attr.get(k) for k in _ATTR_NAME_KEYS))
            val = attr.get("value_name")
            if val is None and isinstance(attr.get("values"), list) and attr["values"]:
                first = attr["values"][0]
                if isinstance(first, dict):
                    val = first.get("name") or first.get("value_name")
                else:
                    val = first
            val_s = _first_str(val, attr.get("value"))
            if key and val_s:
                mapped[str(key)] = val_s
    elif isinstance(attrs, dict):
        mapped = {str(k): str(v) for k, v in attrs.items() if v is not None}
    _ingest_specs(store, mapped, source=source, method=method)


def _find_productish_dicts(payload: Any, *, _out: Optional[list] = None, _depth: int = 0) -> list[dict[str, Any]]:
    found = _out if _out is not None else []
    if _depth > 8 or len(found) > 12:
        return found
    if isinstance(payload, dict):
        keys = set(payload.keys())
        if keys & {"title", "price", "attributes", "catalog_product_id", "permalink", "id"}:
            if "title" in payload or "attributes" in payload or "price" in payload:
                found.append(payload)
        for value in payload.values():
            _find_productish_dicts(value, _out=found, _depth=_depth + 1)
    elif isinstance(payload, list):
        for item in payload[:40]:
            _find_productish_dicts(item, _out=found, _depth=_depth + 1)
    return found


# --- Playwright page helpers -------------------------------------------------

DOM_EXTRACT_JS = """
() => {
  function first(sels, attr) {
    for (const sel of sels) {
      const el = document.querySelector(sel);
      if (!el) continue;
      if (attr) {
        const v = el.getAttribute(attr);
        if (v && v.trim()) return v.trim();
      } else {
        const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        if (t) return t;
      }
    }
    return null;
  }
  function allText(sels) {
    const out = [];
    for (const sel of sels) {
      for (const el of Array.from(document.querySelectorAll(sel))) {
        const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        if (t) out.push(t);
      }
    }
    return out;
  }
  function allAttr(sels, attr) {
    const out = [];
    for (const sel of sels) {
      for (const el of Array.from(document.querySelectorAll(sel))) {
        const v = el.getAttribute(attr);
        if (v && v.trim()) out.push(v.trim());
      }
    }
    return out;
  }
  const titleSels = ['h1.ui-pdp-title', 'h1[class*="title"]', 'h1'];
  const priceSels = [
    '.ui-pdp-price__second-line .andes-money-amount__fraction',
    '.ui-pdp-price .andes-money-amount--cents-superscript .andes-money-amount__fraction',
    '.andes-money-amount__fraction',
    '[itemprop="price"]'
  ];
  const centsSels = ['.ui-pdp-price__second-line .andes-money-amount__cents', '.andes-money-amount__cents'];
  const wasSels = [
    '.ui-pdp-price__original-value .andes-money-amount__fraction',
    '.andes-money-amount--previous .andes-money-amount__fraction'
  ];
  const promoSels = ['.andes-money-amount__discount', '.ui-pdp-price__discount', '[class*="pdp-price__discount"]'];
  const stockSels = ['.ui-pdp-stock-information', '.ui-pdp-buybox__quantity__available', '[class*="stock"]'];
  const badgeSels = [
    '.ui-pdp-gallery img',
    '.ui-pdp-container img',
    '[class*="badge"] img',
    '[class*="pdp-highlights"] img',
    'img[alt*="Intel"]', 'img[alt*="AMD"]', 'img[alt*="Ryzen"]',
    'img[alt*="Core"]', 'img[alt*="Snapdragon"]', 'img[alt*="vPro"]', 'img[alt*="Evo"]'
  ];
  const badgeTextSels = [
    '[class*="badge"]',
    '.ui-pdp-promotions-badge',
    '.pdp-highlights',
    '[class*="highlight"]'
  ];
  const specRows = [];
  const rowSels = [
    'table.andes-table tr',
    '.ui-pdp-specs__table tr',
    '.ui-vpp-striped-specs__table tr',
    'tr.andes-table__row',
    '.ui-pdp-specs__item',
    '.ui-vpp-striped-specs__row',
    'dl.andes-list dt'
  ];
  function pushPair(k, v) {
    const key = (k || '').replace(/\\s+/g, ' ').trim();
    const val = (v || '').replace(/\\s+/g, ' ').trim();
    if (key && val && key.length < 80) specRows.push({key, value: val});
  }
  for (const sel of rowSels) {
    for (const row of Array.from(document.querySelectorAll(sel))) {
      const cells = row.querySelectorAll('th, td, dt, dd, .andes-table__header, .andes-table__column');
      if (cells.length >= 2) {
        pushPair(cells[0].innerText, cells[1].innerText);
      }
    }
  }
  const hiSels = ['.ui-pdp-highlighted-specs-key-value', '.ui-vpp-highlighted-specs__key-value'];
  for (const sel of hiSels) {
    for (const row of Array.from(document.querySelectorAll(sel))) {
      const k = row.querySelector('.ui-pdp-highlighted-specs-key-value__key, .andes-table__header, dt, span');
      const v = row.querySelector('.ui-pdp-highlighted-specs-key-value__value, .andes-table__column, dd');
      if (k && v) pushPair(k.innerText, v.innerText);
    }
  }
  const fraction = first(priceSels, null);
  const cents = first(centsSels, null);
  let price = fraction;
  if (fraction && cents && fraction.indexOf(',') < 0) price = fraction + ',' + cents;
  const h1 = document.querySelector('h1');
  return {
    title: first(titleSels, null),
    title_attr: first(titleSels, 'title'),
    aria_title: first(titleSels, 'aria-label') || (h1 && h1.getAttribute('aria-label')),
    price_text: price,
    price_meta: first(['[itemprop="price"]'], 'content'),
    list_price: first(wasSels, null),
    promo_text: first(promoSels, null),
    availability: first(stockSels, null),
    spec_rows: specRows.slice(0, 120),
    img_alts: allAttr(['img'], 'alt').slice(0, 40),
    img_titles: allAttr(['img'], 'title').slice(0, 40),
    aria_labels: allAttr(['[aria-label]'], 'aria-label').slice(0, 40),
    element_titles: allAttr(['[title]'], 'title').slice(0, 40),
    badge_texts: allText(badgeTextSels).slice(0, 30),
    badge_img_alts: allAttr(badgeSels, 'alt').slice(0, 30),
  };
}
"""

EMBEDDED_EXTRACT_JS = """
() => {
  const blobs = [];
  const scripts = document.querySelectorAll('script:not([src])');
  for (const s of Array.from(scripts)) {
    const t = s.textContent || '';
    if (t.length < 40 || t.length > 1500000) continue;
    if (/__PRELOADED_STATE__|__INITIAL_STATE__|__NEXT_DATA__|preloadedState|application\\/ld\\+json/.test(t)
        && /title|price|attributes|Product/.test(t)) {
      blobs.push(t.slice(0, 400000));
    }
  }
  let state = null;
  try {
    if (window.__PRELOADED_STATE__) state = window.__PRELOADED_STATE__;
  } catch (e) {}
  try {
    if (!state && window.__INITIAL_STATE__) state = window.__INITIAL_STATE__;
  } catch (e) {}
  const ld = [];
  for (const s of Array.from(document.querySelectorAll('script[type="application/ld+json"]'))) {
    if (s.textContent) ld.push(s.textContent);
  }
  return { blobs, state, json_ld_scripts: ld };
}
"""


def apply_dom_payload(store: ProvenanceStore, payload: dict[str, Any]) -> None:
    store.mark_layer("visible_dom")
    title = _first_str(payload.get("title"))
    if title:
        store.set_if_empty(
            "title",
            observation(title, source=SOURCE_PRODUCT_PAGE, extraction_method=METHOD_DOM_TEXT),
        )
    price = _first_str(payload.get("price_text"), payload.get("price_meta"))
    if price:
        store.set_if_empty(
            "price",
            observation(
                price,
                source=SOURCE_PRODUCT_PAGE,
                extraction_method=METHOD_DOM_TEXT,
                currency="BRL",
            ),
        )
    list_price = _first_str(payload.get("list_price"))
    if list_price:
        store.set_if_empty(
            "list_price",
            observation(
                list_price,
                source=SOURCE_PRODUCT_PAGE,
                extraction_method=METHOD_DOM_TEXT,
                currency="BRL",
            ),
        )
    promo = _first_str(payload.get("promo_text"))
    if promo:
        store.set_if_empty(
            "promo_text",
            observation(promo, source=SOURCE_PRODUCT_PAGE, extraction_method=METHOD_DOM_TEXT),
        )
    avail = _first_str(payload.get("availability"))
    if avail:
        store.set_if_empty(
            "availability",
            observation(avail, source=SOURCE_PRODUCT_PAGE, extraction_method=METHOD_DOM_TEXT),
        )
    rows = payload.get("spec_rows") or []
    specs: dict[str, str] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = _first_str(row.get("key"))
            val = _first_str(row.get("value"))
            if key and val:
                specs[key] = val
    _ingest_specs(store, specs, source=SOURCE_PRODUCT_PAGE, method=METHOD_DOM_TEXT)

    store.mark_layer("aria_alt_title")
    aria_title = _first_str(payload.get("aria_title"), payload.get("title_attr"))
    if aria_title:
        store.set_if_empty(
            "title",
            observation(aria_title, source=SOURCE_ARIA, extraction_method=METHOD_DOM_ARIA),
        )


def collect_badge_signals(payload: dict[str, Any]) -> dict[str, list[str]]:
    def _clean(values: Any) -> list[str]:
        out: list[str] = []
        if not isinstance(values, list):
            return out
        for item in values:
            text = _as_text(item)
            if text and text not in out and len(text) < 240:
                out.append(text)
        return out

    return {
        "badge_texts": _clean(payload.get("badge_texts")),
        "img_alts": _clean(payload.get("img_alts")) + _clean(payload.get("badge_img_alts")),
        "img_titles": _clean(payload.get("img_titles")),
        "element_titles": _clean(payload.get("element_titles")),
        "aria_labels": _clean(payload.get("aria_labels")),
    }


def is_network_payload_useful(url: str, content_type: str) -> bool:
    u = (url or "").lower()
    ct = (content_type or "").lower()
    if "mercadoli" not in u:
        return False
    if any(
        skip in u
        for skip in (
            "melidata",
            "analytics",
            "google",
            "facebook",
            "hotjar",
            "tracking",
            "metrics",
            "/track",
            "captcha",
            "account-verification",
        )
    ):
        return False
    if "json" in ct:
        return True
    if "/p/api/" in u or "/products/" in u or "frontend-api" in u:
        return True
    return False


__all__ = [
    "DOM_EXTRACT_JS",
    "EMBEDDED_EXTRACT_JS",
    "FieldObservation",
    "apply_dom_payload",
    "apply_embedded_or_network",
    "apply_json_ld",
    "apply_listing_card",
    "apply_title_heuristics",
    "collect_badge_signals",
    "is_network_payload_useful",
    "parse_embedded_script_text",
    "parse_json_ld_products",
    "pick_spec",
    "specs_from_title",
]
