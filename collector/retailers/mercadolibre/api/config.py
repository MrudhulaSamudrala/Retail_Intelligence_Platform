"""Environment-based official Mercado Libre API configuration.

Credentials are never hardcoded. Missing credentials disable the adapter.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _env(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


@dataclass(frozen=True)
class MercadoLibreApiConfig:
    enabled: bool
    client_id: str
    client_secret: str
    access_token: str
    refresh_token: str
    site_id: str = "MLB"
    base_url: str = "https://api.mercadolibre.com"
    timeout_seconds: float = 20.0

    def has_access_token(self) -> bool:
        return bool(self.access_token)

    def can_refresh(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)


def load_api_config(
    environ: Optional[dict[str, str]] = None,
) -> MercadoLibreApiConfig:
    """Load config from os.environ (or an injected mapping for tests)."""
    getenv = (environ or os.environ).get

    def read(*names: str) -> str:
        for name in names:
            value = str(getenv(name) or "").strip()
            if value:
                return value
        return ""

    access_token = read("MERCADOLIBRE_API_ACCESS_TOKEN", "ML_ACCESS_TOKEN")
    client_id = read("MERCADOLIBRE_API_CLIENT_ID", "ML_CLIENT_ID")
    client_secret = read("MERCADOLIBRE_API_CLIENT_SECRET", "ML_CLIENT_SECRET")
    refresh_token = read("MERCADOLIBRE_API_REFRESH_TOKEN", "ML_REFRESH_TOKEN")
    forced = read("MERCADOLIBRE_API_ENABLED", "ML_API_ENABLED").lower()
    has_creds = bool(access_token or (client_id and client_secret and refresh_token))
    if forced in {"0", "false", "no", "off"}:
        enabled = False
    elif forced in {"1", "true", "yes", "on"}:
        enabled = has_creds
    else:
        enabled = has_creds

    timeout_raw = read("MERCADOLIBRE_API_TIMEOUT_SECONDS") or "20"
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 20.0

    return MercadoLibreApiConfig(
        enabled=enabled,
        client_id=client_id,
        client_secret=client_secret,
        access_token=access_token,
        refresh_token=refresh_token,
        site_id=read("MERCADOLIBRE_API_SITE_ID") or "MLB",
        timeout_seconds=timeout,
    )
