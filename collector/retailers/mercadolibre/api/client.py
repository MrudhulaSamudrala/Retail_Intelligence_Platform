"""Read-only HTTP client for official Mercado Libre APIs."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from collector.evidence import (
    REASON_API_AUTH_FAILED,
    REASON_API_DISABLED,
    REASON_API_ITEM_NOT_FOUND,
    REASON_API_MALFORMED,
    REASON_API_RATE_LIMITED,
    REASON_API_UNAVAILABLE,
)
from collector.retailers.mercadolibre.api.config import MercadoLibreApiConfig, load_api_config
from collector.retailers.mercadolibre.listing import extract_mlb_id, normalize_mlb_id

logger = logging.getLogger("collector.mercadolibre.api")

STATUS_OK = "OK"

HttpOpener = Callable[..., Any]


@dataclass
class ApiCallResult:
    status: str
    endpoint: Optional[str] = None
    http_status: Optional[int] = None
    payload: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    sku_requested: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "endpoint": self.endpoint,
            "http_status": self.http_status,
            "error": self.error,
            "sku_requested": self.sku_requested,
            "has_payload": bool(self.payload),
        }


@dataclass
class LookupResult:
    """Combined catalog/item lookup for one retailer SKU."""

    status: str
    item: Optional[ApiCallResult] = None
    product: Optional[ApiCallResult] = None
    calls: list[ApiCallResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "item": self.item.to_dict() if self.item else None,
            "product": self.product.to_dict() if self.product else None,
            "calls": [c.to_dict() for c in self.calls],
        }

    def payloads(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for call in (self.product, self.item):
            if call and isinstance(call.payload, dict):
                out.append(call.payload)
        return out


class MercadoLibreApiClient:
    def __init__(
        self,
        config: Optional[MercadoLibreApiConfig] = None,
        *,
        opener: Optional[HttpOpener] = None,
    ) -> None:
        self.config = config or load_api_config()
        self._opener = opener or urlopen
        self._access_token = self.config.access_token
        self._refresh_token = self.config.refresh_token

    def lookup(self, sku: str, *, source_url: str = "") -> LookupResult:
        if not self.config.enabled:
            return LookupResult(status=REASON_API_DISABLED)
        mlb = extract_mlb_id(source_url, sku) or normalize_mlb_id(sku)
        if not mlb:
            return LookupResult(status=REASON_API_ITEM_NOT_FOUND)

        calls: list[ApiCallResult] = []
        product_call = self.get_product(mlb)
        calls.append(product_call)
        if product_call.status == REASON_API_RATE_LIMITED:
            return LookupResult(status=REASON_API_RATE_LIMITED, product=None, calls=calls)
        item_call: Optional[ApiCallResult] = None
        if product_call.status != STATUS_OK:
            item_call = self.get_item(mlb)
            calls.append(item_call)
            if item_call.status == REASON_API_RATE_LIMITED:
                return LookupResult(
                    status=REASON_API_RATE_LIMITED,
                    item=None,
                    calls=calls,
                )
            if item_call.status == STATUS_OK and isinstance(item_call.payload, dict):
                catalog_id = item_call.payload.get("catalog_product_id")
                if catalog_id and str(catalog_id) != mlb:
                    extra = self.get_product(str(catalog_id))
                    calls.append(extra)
                    if extra.status == STATUS_OK:
                        product_call = extra
        elif isinstance(product_call.payload, dict):
            attrs = product_call.payload.get("attributes")
            if not attrs:
                item_call = self.get_item(mlb)
                calls.append(item_call)

        winning = _winning_status(calls)
        return LookupResult(
            status=winning,
            item=item_call if item_call and item_call.status == STATUS_OK else None,
            product=product_call if product_call.status == STATUS_OK else None,
            calls=calls,
        )

    def get_item(self, item_id: str) -> ApiCallResult:
        mlb = normalize_mlb_id(item_id)
        path = f"/items/{mlb}?include_attributes=all"
        return self._get_json(path, sku=mlb)

    def get_product(self, product_id: str) -> ApiCallResult:
        mlb = normalize_mlb_id(product_id)
        return self._get_json(f"/products/{mlb}", sku=mlb)

    def _get_json(self, path: str, *, sku: str, retry_auth: bool = True) -> ApiCallResult:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        headers = {"Accept": "application/json", "User-Agent": "BridgeAI-collector/1.0"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        try:
            status_code, body = self._request("GET", url, headers=headers)
        except TimeoutError as exc:
            return ApiCallResult(
                status=REASON_API_UNAVAILABLE,
                endpoint=path.split("?")[0],
                error=str(exc),
                sku_requested=sku,
            )
        except URLError as exc:
            return ApiCallResult(
                status=REASON_API_UNAVAILABLE,
                endpoint=path.split("?")[0],
                error=str(exc.reason if getattr(exc, "reason", None) else exc),
                sku_requested=sku,
            )

        if status_code == 401 and retry_auth and self._try_refresh():
            return self._get_json(path, sku=sku, retry_auth=False)
        if status_code == 429:
            return ApiCallResult(
                status=REASON_API_RATE_LIMITED,
                endpoint=path.split("?")[0],
                http_status=status_code,
                sku_requested=sku,
            )
        if status_code in {401, 403}:
            return ApiCallResult(
                status=REASON_API_AUTH_FAILED,
                endpoint=path.split("?")[0],
                http_status=status_code,
                sku_requested=sku,
            )
        if status_code == 404:
            return ApiCallResult(
                status=REASON_API_ITEM_NOT_FOUND,
                endpoint=path.split("?")[0],
                http_status=status_code,
                sku_requested=sku,
            )
        if status_code >= 500:
            return ApiCallResult(
                status=REASON_API_UNAVAILABLE,
                endpoint=path.split("?")[0],
                http_status=status_code,
                sku_requested=sku,
            )
        if status_code >= 400:
            return ApiCallResult(
                status=REASON_API_UNAVAILABLE,
                endpoint=path.split("?")[0],
                http_status=status_code,
                sku_requested=sku,
            )
        if not isinstance(body, dict) or body.get("error"):
            return ApiCallResult(
                status=REASON_API_MALFORMED,
                endpoint=path.split("?")[0],
                http_status=status_code,
                payload=body if isinstance(body, dict) else None,
                error=str((body or {}).get("error") if isinstance(body, dict) else "non_object"),
                sku_requested=sku,
            )
        return ApiCallResult(
            status=STATUS_OK,
            endpoint=path.split("?")[0],
            http_status=status_code,
            payload=body,
            sku_requested=sku,
        )

    def _try_refresh(self) -> bool:
        if not self.config.can_refresh():
            return False
        url = f"{self.config.base_url.rstrip('/')}/oauth/token"
        form = urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": self._refresh_token,
            }
        ).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            status_code, body = self._request("POST", url, headers=headers, data=form)
        except (TimeoutError, URLError):
            return False
        if status_code != 200 or not isinstance(body, dict):
            return False
        token = str(body.get("access_token") or "").strip()
        if not token:
            return False
        self._access_token = token
        new_refresh = str(body.get("refresh_token") or "").strip()
        if new_refresh:
            self._refresh_token = new_refresh
        logger.info(
            "mercadolibre_api_token_refreshed",
            extra={"event": "mercadolibre_api_token_refreshed"},
        )
        return True

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        data: Optional[bytes] = None,
    ) -> tuple[int, Any]:
        request = Request(url, data=data, headers=headers, method=method)
        timeout = self.config.timeout_seconds
        try:
            with self._opener(request, timeout=timeout) as response:
                raw = response.read()
                status_code = int(getattr(response, "status", None) or response.getcode())
        except HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            status_code = int(exc.code)
        text = raw.decode("utf-8", errors="replace") if raw else ""
        if not text:
            return status_code, None
        try:
            return status_code, json.loads(text)
        except json.JSONDecodeError:
            return status_code, {"error": "malformed_json", "raw_preview": text[:200]}


def _winning_status(calls: list[ApiCallResult]) -> str:
    if any(c.status == STATUS_OK for c in calls):
        return STATUS_OK
    order = (
        REASON_API_RATE_LIMITED,
        REASON_API_AUTH_FAILED,
        REASON_API_UNAVAILABLE,
        REASON_API_MALFORMED,
        REASON_API_ITEM_NOT_FOUND,
    )
    present = {c.status for c in calls}
    for status in order:
        if status in present:
            return status
    return calls[-1].status if calls else REASON_API_UNAVAILABLE
