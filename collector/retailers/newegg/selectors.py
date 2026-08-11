"""Newegg US CSS/text selectors and patterns.

Validated against live Newegg markup where accessible; multiple fallbacks
are provided because Newegg frequently A/B tests listing templates.
"""

LISTING_ITEM_SELECTORS = [
    ".item-cell",
    ".item-container",
    "div.product-item",
    "[class*='product-item']",
]

LISTING_TITLE_SELECTORS = [
    "a.item-title",
    ".item-title",
    "a[title]",
    "a[href*='/p/']",
]

LISTING_PRICE_SELECTORS = [
    ".price-current",
    "li.price-current",
    "[class*='price-current']",
    ".goods-price",
]

LISTING_WAS_PRICE_SELECTORS = [
    ".price-was",
    "li.price-was",
    "[class*='price-was']",
]

LISTING_PROMO_SELECTORS = [
    ".item-promo",
    ".price-save",
    ".price-note",
    "[class*='item-promo']",
]

PRODUCT_TITLE_SELECTORS = [
    "h1.product-title",
    "h1[class*='product-title']",
    "h1.product-name",
    "h1",
]

PRODUCT_PRICE_SELECTORS = [
    ".product-price .price-current",
    ".price-current",
    "#landingpage-price .price-current",
    "[class*='price-current']",
]

PRODUCT_AVAILABILITY_SELECTORS = [
    "#landingpage-cart .product-buy-box",
    ".product-inventory",
    "#ProductBuy",
    "button[title*='Add to cart' i]",
    "button:has-text('Add to cart')",
    "button:has-text('Sold Out')",
]

SPEC_ROW_SELECTORS = [
    "#product-details table tr",
    "#Specifications table tr",
    "table.table-horizontal tr",
    "[id*='Specifications'] table tr",
    "div#product-details dl",
]

BOT_CHALLENGE_MARKERS = [
    "unusual traffic",
    "are you a human",
    "just a moment",
    "cf-browser-verification",
    "attention required",
]
