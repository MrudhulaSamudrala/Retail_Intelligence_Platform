"""Mercado Libre (Brazil) DOM selectors — retailer-specific only."""

from __future__ import annotations

# Listing / search / ofertas cards (modern poly-card layout)
LISTING_CARD_SELECTORS = [
    "div.poly-card",
    "li.ui-search-layout__item",
    "div.ui-search-result",
    ".ui-search-result__wrapper",
]

LISTING_TITLE_SELECTORS = [
    "a.poly-component__title",
    "h2.ui-search-item__title",
    "a.ui-search-link",
    "a.ui-search-item__group__element",
]

LISTING_PRICE_SELECTORS = [
    ".poly-price__current .andes-money-amount__fraction",
    ".andes-money-amount__fraction",
    ".poly-component__price .andes-money-amount__fraction",
]

LISTING_PRICE_CENTS_SELECTORS = [
    ".poly-price__current .andes-money-amount__cents",
    ".andes-money-amount__cents",
]

LISTING_WAS_PRICE_SELECTORS = [
    ".andes-money-amount--previous .andes-money-amount__fraction",
    "s .andes-money-amount__fraction",
    ".poly-price__origin .andes-money-amount__fraction",
]

LISTING_DISCOUNT_SELECTORS = [
    ".andes-money-amount__discount",
    ".poly-price__discount",
    "[class*='discount']",
]

LISTING_ATTRIBUTE_SELECTORS = [
    "ul.poly-attributes_list li",
    "ul.ui-search-card-attributes li",
    ".poly-component__attributes",
]

LISTING_BADGE_SELECTORS = [
    ".poly-component__badges img",
    ".poly-component__cbt img",
    "[class*='poly-component__'] img",
]

# Product detail page
PRODUCT_TITLE_SELECTORS = [
    "h1.ui-pdp-title",
    "h1[class*='title']",
    "h1",
]

PRODUCT_PRICE_SELECTORS = [
    ".ui-pdp-price__second-line .andes-money-amount__fraction",
    ".andes-money-amount__fraction",
    "[itemprop='price']",
]

PRODUCT_WAS_PRICE_SELECTORS = [
    ".ui-pdp-price__original-value .andes-money-amount__fraction",
    ".andes-money-amount--previous .andes-money-amount__fraction",
]

PRODUCT_AVAILABILITY_SELECTORS = [
    ".ui-pdp-stock-information",
    ".ui-pdp-buybox__quantity__available",
    "[class*='stock']",
]

SPEC_ROW_SELECTORS = [
    "table.andes-table tr",
    ".ui-pdp-specs__table tr",
    ".ui-vpp-striped-specs__table tr",
    "tr.andes-table__row",
    ".ui-pdp-specs__item",
    ".ui-vpp-striped-specs__row",
    ".ui-pdp-highlighted-specs-key-value",
]

ACCOUNT_VERIFICATION_MARKERS = [
    "account-verification",
    "para continuar, acesse",
    "olá! para continuar",
    "ola! para continuar",
]

BOT_CHALLENGE_MARKERS = [
    "unusual traffic",
    "captcha",
    "are you a human",
    "access denied",
]
