"""Official Mercado Libre API adapter (optional, read-only).

Verified endpoints (developers.mercadolibre.com, Brazil site MLB):

- GET https://api.mercadolibre.com/items/$ITEM_ID
- GET https://api.mercadolibre.com/items/$ITEM_ID?include_attributes=all
- GET https://api.mercadolibre.com/items?ids=$ID1,$ID2
- GET https://api.mercadolibre.com/products/$PRODUCT_ID
- POST https://api.mercadolibre.com/oauth/token

Does not implement write/listing APIs. Disabled when credentials are absent.
"""

from collector.retailers.mercadolibre.api.config import MercadoLibreApiConfig, load_api_config
from collector.retailers.mercadolibre.api.enrich import enrich_product

__all__ = [
    "MercadoLibreApiConfig",
    "enrich_product",
    "load_api_config",
]
