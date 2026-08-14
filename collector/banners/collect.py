"""Playwright homepage banner collection (no product discovery)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from playwright.async_api import Page

from collector.banners.detect import DetectedBanner, load_banner_config, process_banner_candidates
from collector.browser import BrowserSession
from collector.config_loader import get_retailer, load_retailers

logger = logging.getLogger("collector.banners.collect")

# Runs in the page to extract candidate banner-like nodes without inventing content.
_EXTRACT_CANDIDATES_JS = """
(args) => {
  const selectors = args.selectors || [];
  const excludeSelectors = args.excludeSelectors || [];
  const maxNodes = args.maxNodes || 40;

  function cssPath(el) {
    if (!el || el.nodeType !== 1) return null;
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        part += '#' + CSS.escape(node.id);
        parts.unshift(part);
        break;
      }
      const cls = (node.className && typeof node.className === 'string')
        ? node.className.trim().split(/\\s+/).slice(0, 2).join('.')
        : '';
      if (cls) part += '.' + cls.replace(/\\./g, '\\\\.');
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(' > ');
  }

  function isExcluded(el) {
    for (const sel of excludeSelectors) {
      try {
        if (el.closest(sel)) return true;
      } catch (e) {}
    }
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'nav' || tag === 'footer' || tag === 'table') return true;
    return false;
  }

  function visible(el) {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
      return false;
    }
    const rect = el.getBoundingClientRect();
    if (rect.width < 40 || rect.height < 30) return false;
    // Must intersect viewport or be near top of page (hero carousels)
    if (rect.bottom < 0) return false;
    if (rect.top > (window.innerHeight * 2.5)) return false;
    return true;
  }

  const seen = new Set();
  const out = [];
  for (const sel of selectors) {
    let nodes = [];
    try { nodes = Array.from(document.querySelectorAll(sel)); } catch (e) { continue; }
    for (const el of nodes) {
      if (out.length >= maxNodes) break;
      if (!el || seen.has(el)) continue;
      if (isExcluded(el)) continue;
      if (!visible(el)) continue;
      seen.add(el);

      const imgs = Array.from(el.querySelectorAll('img')).slice(0, 6);
      const alts = imgs.map(i => i.getAttribute('alt') || '').filter(Boolean);
      const titles = [
        el.getAttribute('title') || '',
        ...imgs.map(i => i.getAttribute('title') || ''),
      ].filter(Boolean);
      const imageUrls = [];
      function pushUrl(v) {
        if (!v || typeof v !== 'string') return;
        const trimmed = v.trim();
        if (!trimmed || trimmed.startsWith('data:')) return;
        if (!imageUrls.includes(trimmed)) imageUrls.push(trimmed);
      }
      for (const i of imgs) {
        pushUrl(i.currentSrc || '');
        pushUrl(i.getAttribute('src') || '');
        pushUrl(i.getAttribute('data-src') || '');
        pushUrl(i.getAttribute('data-lazy') || '');
        pushUrl(i.getAttribute('data-original') || '');
        const srcset = i.getAttribute('srcset') || '';
        if (srcset) pushUrl(srcset.split(',')[0].trim().split(/\\s+/)[0]);
      }
      const sources = Array.from(el.querySelectorAll('source')).slice(0, 4);
      for (const s of sources) {
        pushUrl(s.getAttribute('src') || '');
        const srcset = s.getAttribute('srcset') || '';
        if (srcset) pushUrl(srcset.split(',')[0].trim().split(/\\s+/)[0]);
      }
      let bgNode = el;
      for (let d = 0; d < 3 && bgNode; d++) {
        const bg = window.getComputedStyle(bgNode).backgroundImage || '';
        const matches = bg.matchAll(/url\\((["']?)([^"')]+)\\1\\)/gi);
        for (const m of matches) pushUrl(m[2]);
        bgNode = bgNode.parentElement;
      }
      const link = el.closest('a') || el.querySelector('a');
      const href = link ? (link.href || link.getAttribute('href') || '') : '';
      const text = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 1200);
      const aria = el.getAttribute('aria-label')
        || (link ? (link.getAttribute('aria-label') || '') : '');
      const ancestorHints = [];
      let p = el.parentElement;
      for (let i = 0; i < 4 && p; i++) {
        ancestorHints.push((p.className && typeof p.className === 'string') ? p.className : p.tagName);
        p = p.parentElement;
      }

      out.push({
        tag: el.tagName.toLowerCase(),
        class_name: (typeof el.className === 'string') ? el.className : '',
        role: el.getAttribute('role') || '',
        text: text,
        aria_label: aria,
        alt: alts.join(' | '),
        title: titles.join(' | '),
        href: href,
        image_url: imageUrls.slice(0, 8).join(' | '),
        selector: cssPath(el) || sel,
        ancestor_hints: ancestorHints,
        position: out.length + 1,
      });
    }
  }
  return out;
}
"""


@dataclass
class HomepageInspectionResult:
    retailer_code: str
    country_code: str
    homepage_url: str
    inspected: bool
    banners: list[DetectedBanner] = field(default_factory=list)
    screenshot_path: Optional[str] = None
    error: Optional[str] = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _selectors_for_retailer(retailer_code: str, cfg: dict[str, Any]) -> list[str]:
    mapping = cfg.get("banner_selectors") or {}
    specific = list(mapping.get(retailer_code) or [])
    default = list(mapping.get("default") or [])
    # Prefer retailer-specific first, then defaults (dedupe)
    seen: set[str] = set()
    out: list[str] = []
    for sel in specific + default:
        if sel not in seen:
            seen.add(sel)
            out.append(sel)
    return out


async def extract_candidates_from_page(
    page: Page,
    *,
    retailer_code: str,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = config or load_banner_config()
    selectors = _selectors_for_retailer(retailer_code, cfg)
    exclude = list(cfg.get("exclude_selectors") or [])
    return await page.evaluate(
        _EXTRACT_CANDIDATES_JS,
        {"selectors": selectors, "excludeSelectors": exclude, "maxNodes": 40},
    )


async def inspect_retailer_homepage(
    session: BrowserSession,
    *,
    retailer_code: str,
    country_code: str | None = None,
    homepage_url: str | None = None,
) -> HomepageInspectionResult:
    """Open one retailer homepage and detect visible brand promotional banners."""
    retailer = get_retailer(retailer_code)
    country = country_code or str(retailer.get("country_code") or "")
    url = homepage_url or str(retailer.get("base_url") or "")
    observed_at = datetime.now(timezone.utc)
    result = HomepageInspectionResult(
        retailer_code=retailer_code,
        country_code=country,
        homepage_url=url,
        inspected=False,
        observed_at=observed_at,
    )
    if not url:
        result.error = "missing_base_url"
        return result

    page = await session.new_page()
    try:
        await session.goto(page, url, wait_until="domcontentloaded")
        # Allow hero carousels to settle without inventing content.
        await page.wait_for_timeout(2500)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001
            pass

        page_shot = await session.screenshot(
            page, label=f"homepage_{retailer_code}", full_page=False
        )
        result.screenshot_path = page_shot

        title = await page.title()
        content = (await page.content())[:2000].lower()
        if "unusual traffic" in title.lower() or "captcha" in content or "cf-challenge" in content:
            result.error = f"bot_challenge_or_block title={title!r}"
            result.inspected = False
            return result

        candidates = await extract_candidates_from_page(page, retailer_code=retailer_code)
        banners = process_banner_candidates(candidates, source_url=page.url)
        for banner in banners:
            banner.source_url = page.url
            banner.screenshot_path = banner.screenshot_path or page_shot
        result.banners = banners
        result.inspected = True
        result.homepage_url = page.url
        logger.info(
            "homepage_inspected",
            extra={
                "event": "homepage_inspected",
                "retailer": retailer_code,
                "country": country,
                "url": page.url,
                "count": len(banners),
            },
        )
        return result
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
        logger.warning(
            "homepage_inspection_failed",
            extra={
                "event": "homepage_inspection_failed",
                "retailer": retailer_code,
                "error": str(exc),
                "url": url,
            },
        )
        return result
    finally:
        await page.close()


async def collect_homepage_banners(
    *,
    retailer_codes: list[str] | None = None,
    browser: BrowserSession | None = None,
) -> list[HomepageInspectionResult]:
    """Inspect configured retailer homepages (no product collection)."""
    retailers_cfg = load_retailers().get("retailers") or []
    enabled = [
        r
        for r in retailers_cfg
        if r.get("enabled") and (retailer_codes is None or r.get("code") in retailer_codes)
    ]
    owns = browser is None
    session = browser or BrowserSession()
    results: list[HomepageInspectionResult] = []
    try:
        if owns:
            await session.__aenter__()
        for retailer in enabled:
            code = str(retailer["code"])
            results.append(
                await inspect_retailer_homepage(
                    session,
                    retailer_code=code,
                    country_code=str(retailer.get("country_code") or ""),
                    homepage_url=str(retailer.get("base_url") or ""),
                )
            )
    finally:
        if owns:
            await session.__aexit__(None, None, None)
    return results
