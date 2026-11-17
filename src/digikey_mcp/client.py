"""DigiKey Product Search API v4 client.

Nothing in this module touches the network or the environment at import time.
Credentials are passed to the constructor or read by `load_credentials`.
"""

from __future__ import annotations

import copy
import email.utils
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

PRODUCTION_BASE_URL = "https://api.digikey.com"
SANDBOX_BASE_URL = "https://sandbox-api.digikey.com"
TOKEN_PATH = "/v1/oauth2/token"
API_PREFIX = "/products/v4/search"

_TRUE = {"1", "true", "yes", "on"}
_MAX_ERROR_TEXT = 300


class DigiKeyError(Exception):
    """Any failure talking to DigiKey. `status` is the HTTP status when known."""

    def __init__(self, message: str, status: int | None = None, correlation_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.correlation_id = correlation_id


class DigiKeyConfigError(DigiKeyError):
    """Credentials or settings are missing or unreadable."""


class DigiKeyAuthError(DigiKeyError):
    """The OAuth2 token request, or a retried authenticated request, was rejected."""


def _read_setting(name: str, env: Mapping[str, str]) -> str | None:
    file_var = f"{name}_FILE"
    path = env.get(file_var)
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError as exc:
            raise DigiKeyConfigError(f"{file_var} points to an unreadable file: {exc.strerror}") from exc
        if not value:
            raise DigiKeyConfigError(f"{file_var} points to an empty file")
        return value
    value = env.get(name)
    return value.strip() if value and value.strip() else None


@dataclass(frozen=True)
class CredentialCheck:
    """State of one credential (CLIENT_ID or CLIENT_SECRET); never holds the value."""

    name: str
    #: "file" (NAME_FILE is set), "variable" (NAME is set) or "unset"
    source: str
    path: str | None
    #: None when usable, else why not: "unset", "file not found", "file unreadable", "file empty"
    problem: str | None

    @property
    def ok(self) -> bool:
        return self.problem is None

    def describe(self) -> str:
        """One phrase naming what is wrong, for warnings."""
        if self.problem == "unset":
            return f"{self.name} not set"
        return f"{self.name}_FILE: {self.problem}"


def check_credential(name: str, env: Mapping[str, str]) -> CredentialCheck:
    """Resolve one credential the way `_read_setting` does, without returning its value."""
    path = env.get(f"{name}_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                value = fh.read().strip()
        except FileNotFoundError:
            return CredentialCheck(name, "file", path, "file not found")
        except OSError:
            return CredentialCheck(name, "file", path, "file unreadable")
        return CredentialCheck(name, "file", path, None if value else "file empty")
    value = env.get(name)
    if value and value.strip():
        return CredentialCheck(name, "variable", None, None)
    return CredentialCheck(name, "unset", None, "unset")


def check_credentials(env: Mapping[str, str] | None = None) -> tuple[CredentialCheck, CredentialCheck]:
    """State of the client ID and the client secret; reads files, makes no network call."""
    env = os.environ if env is None else env
    return check_credential("CLIENT_ID", env), check_credential("CLIENT_SECRET", env)


def sandbox_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True when DIGIKEY_SANDBOX selects sandbox-api.digikey.com."""
    env = os.environ if env is None else env
    return env.get("DIGIKEY_SANDBOX", "").strip().lower() in _TRUE


def load_credentials(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Return (client_id, client_secret).

    Each value comes from CLIENT_ID_FILE / CLIENT_SECRET_FILE (path to a file,
    for systemd credentials or sops-nix) or, failing that, CLIENT_ID /
    CLIENT_SECRET.
    """
    env = os.environ if env is None else env
    client_id = _read_setting("CLIENT_ID", env)
    client_secret = _read_setting("CLIENT_SECRET", env)
    missing = [n for n, v in (("CLIENT_ID", client_id), ("CLIENT_SECRET", client_secret)) if not v]
    if missing:
        raise DigiKeyConfigError(
            f"{' and '.join(missing)} not set (set the variable or its _FILE variant)"
        )
    return client_id, client_secret  # type: ignore[return-value]


def _error_message(resp: httpx.Response) -> tuple[str, str | None]:
    """Extract a short human-readable message and correlation id from an error response."""
    correlation = None
    text = ""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        correlation = body.get("correlationId") or body.get("CorrelationId")
        for key in ("detail", "ErrorMessage", "error_description", "title", "message"):
            if isinstance(body.get(key), str) and body[key].strip():
                text = body[key].strip()
                break
    if not text:
        text = (resp.text or "").strip() or resp.reason_phrase
    return text[:_MAX_ERROR_TEXT], correlation


def _path_segment(value: object) -> str:
    """Percent-encode one path segment, including `/`, `#`, `?` and spaces."""
    return quote(str(value), safe="")


def _csv(value: str | Iterable[str] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return ",".join(value)


class DigiKeyClient:
    """Synchronous client for the 14 DigiKey Product Search v4 endpoints.

    Methods return the decoded JSON body. Errors raise `DigiKeyError`.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        sandbox: bool = False,
        locale_site: str = "US",
        locale_language: str = "en",
        locale_currency: str = "USD",
        customer_id: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        max_retry_wait: float = 60.0,
        cache_ttl: float = 300.0,
        cache_max_entries: int = 512,
        token_margin: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not client_id or not client_secret:
            raise DigiKeyConfigError("client_id and client_secret are required")
        self._client_id = client_id
        self._client_secret = client_secret
        self.sandbox = sandbox
        self.base_url = SANDBOX_BASE_URL if sandbox else PRODUCTION_BASE_URL
        self._locale = {
            "X-DIGIKEY-Locale-Site": locale_site,
            "X-DIGIKEY-Locale-Language": locale_language,
            "X-DIGIKEY-Locale-Currency": locale_currency,
        }
        self._customer_id = customer_id
        self._max_retries = max_retries
        self._max_retry_wait = max_retry_wait
        self._cache_ttl = cache_ttl
        self._cache_max = cache_max_entries
        self._token_margin = token_margin
        self._sleep = sleep
        self._clock = clock
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._lock = threading.RLock()
        self._token: str | None = None
        self._token_expires = 0.0
        self._cache: dict[tuple, tuple[float, Any]] = {}

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: Any) -> DigiKeyClient:
        """Build a client from the environment.

        Reads CLIENT_ID/CLIENT_SECRET (or *_FILE), DIGIKEY_SANDBOX (true selects
        sandbox-api.digikey.com), DIGIKEY_LOCALE_SITE, DIGIKEY_LOCALE_LANGUAGE,
        DIGIKEY_LOCALE_CURRENCY and DIGIKEY_CUSTOMER_ID. Keyword overrides win.
        """
        env = os.environ if env is None else env
        client_id, client_secret = load_credentials(env)
        settings: dict[str, Any] = {
            "sandbox": sandbox_enabled(env),
        }
        for key, var in (
            ("locale_site", "DIGIKEY_LOCALE_SITE"),
            ("locale_language", "DIGIKEY_LOCALE_LANGUAGE"),
            ("locale_currency", "DIGIKEY_LOCALE_CURRENCY"),
            ("customer_id", "DIGIKEY_CUSTOMER_ID"),
        ):
            if env.get(var, "").strip():
                settings[key] = env[var].strip()
        settings.update(overrides)
        return cls(client_id, client_secret, **settings)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DigiKeyClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- auth -----------------------------------------------------------

    def _fetch_token(self) -> str:
        try:
            resp = self._http.post(
                f"{self.base_url}{TOKEN_PATH}",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise DigiKeyAuthError(f"token request failed: {type(exc).__name__}") from None
        if resp.status_code != 200:
            message, correlation = _error_message(resp)
            raise DigiKeyAuthError(
                f"token request rejected ({resp.status_code}): {message}", resp.status_code, correlation
            )
        try:
            body = resp.json()
            token = body["access_token"]
            lifetime = float(body.get("expires_in", 600))
        except (ValueError, KeyError, TypeError):
            raise DigiKeyAuthError("token response was malformed", resp.status_code) from None
        with self._lock:
            self._token = token
            self._token_expires = self._clock() + max(lifetime - self._token_margin, 0.0)
        logger.debug("obtained DigiKey access token (%ss)", lifetime)
        return token

    def authenticate(self) -> None:
        """Request a token now; raises DigiKeyAuthError when DigiKey rejects the credentials."""
        self._get_token()

    def _get_token(self) -> str:
        with self._lock:
            if self._token and self._clock() < self._token_expires:
                return self._token
            return self._fetch_token()

    def _invalidate_token(self, token: str) -> None:
        with self._lock:
            if self._token == token:
                self._token = None

    # ---- transport ------------------------------------------------------

    def _headers(self, token: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "X-DIGIKEY-Client-Id": self._client_id,
            "Accept": "application/json",
            **self._locale,
        }
        if self._customer_id:
            headers["X-DIGIKEY-Customer-Id"] = self._customer_id
        return headers

    def _retry_wait(self, resp: httpx.Response, attempt: int) -> float:
        header = resp.headers.get("Retry-After", "").strip()
        wait: float | None = None
        if header:
            try:
                wait = float(header)
            except ValueError:
                try:
                    wait = email.utils.parsedate_to_datetime(header).timestamp() - time.time()
                except (TypeError, ValueError):
                    wait = None
        if wait is None:
            wait = float(2**attempt)
        return min(max(wait, 0.0), self._max_retry_wait)

    def _cache_get(self, key: tuple) -> Any | None:
        if self._cache_ttl <= 0:
            return None
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry[0] <= self._clock():
                del self._cache[key]
                return None
            return copy.deepcopy(entry[1])

    def _cache_put(self, key: tuple, value: Any) -> None:
        if self._cache_ttl <= 0:
            return
        with self._lock:
            while len(self._cache) >= self._cache_max:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (self._clock() + self._cache_ttl, copy.deepcopy(value))

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{self.base_url}{API_PREFIX}{path}"
        cache_key = None
        if method == "GET":
            cache_key = (url, tuple(sorted(params.items())), tuple(self._locale.values()), self._customer_id)
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        reauthenticated = False
        rate_limited = 0
        while True:
            token = self._get_token()
            try:
                resp = self._http.request(
                    method, url, params=params or None, json=json, headers=self._headers(token)
                )
            except httpx.HTTPError as exc:
                raise DigiKeyError(f"request to DigiKey failed: {type(exc).__name__}") from None

            if resp.status_code == 401 and not reauthenticated:
                reauthenticated = True
                self._invalidate_token(token)
                continue
            if resp.status_code == 429 and rate_limited < self._max_retries:
                wait = self._retry_wait(resp, rate_limited)
                rate_limited += 1
                logger.warning("DigiKey rate limit hit, waiting %.1fs (retry %d)", wait, rate_limited)
                self._sleep(wait)
                continue
            break

        if resp.status_code >= 400:
            message, correlation = _error_message(resp)
            text = f"DigiKey returned {resp.status_code}: {message}"
            if resp.status_code == 429:
                text = f"DigiKey rate limit still exceeded after {rate_limited} retries: {message}"
            cls = DigiKeyAuthError if resp.status_code == 401 else DigiKeyError
            raise cls(text, resp.status_code, correlation)
        try:
            data = resp.json()
        except ValueError:
            raise DigiKeyError("DigiKey returned a non-JSON response", resp.status_code) from None
        if cache_key is not None:
            self._cache_put(cache_key, data)
        return data

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    # ---- endpoints ------------------------------------------------------

    def keyword_search(
        self,
        keywords: str,
        *,
        limit: int = 10,
        offset: int = 0,
        manufacturer_ids: Iterable[int | str] | None = None,
        category_ids: Iterable[int | str] | None = None,
        status_ids: Iterable[int | str] | None = None,
        packaging_ids: Iterable[int | str] | None = None,
        series_ids: Iterable[int | str] | None = None,
        search_options: Iterable[str] | None = None,
        marketplace_filter: str | None = None,
        tariff_filter: str | None = None,
        minimum_quantity_available: int | None = None,
        sort_field: str | None = None,
        sort_order: str = "Ascending",
        includes: str | Iterable[str] | None = None,
    ) -> dict:
        """POST /search/keyword. Filters map onto KeywordRequest.FilterOptionsRequest."""
        if not keywords or not keywords.strip():
            raise ValueError("keywords must not be empty")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        body: dict[str, Any] = {"Keywords": keywords, "Limit": limit, "Offset": offset}
        filters: dict[str, Any] = {}
        for field, ids in (
            ("ManufacturerFilter", manufacturer_ids),
            ("CategoryFilter", category_ids),
            ("StatusFilter", status_ids),
            ("PackagingFilter", packaging_ids),
            ("SeriesFilter", series_ids),
        ):
            if ids:
                filters[field] = [{"Id": str(i)} for i in ids]
        if search_options:
            filters["SearchOptions"] = list(search_options)
        if marketplace_filter:
            filters["MarketPlaceFilter"] = marketplace_filter
        if tariff_filter:
            filters["TariffFilter"] = tariff_filter
        if minimum_quantity_available is not None:
            filters["MinimumQuantityAvailable"] = minimum_quantity_available
        if filters:
            body["FilterOptionsRequest"] = filters
        if sort_field:
            body["SortOptions"] = {"Field": sort_field, "SortOrder": sort_order}
        return self._request("POST", "/keyword", params={"includes": _csv(includes)}, json=body)

    def product_details(
        self,
        product_number: str,
        *,
        manufacturer_id: int | str | None = None,
        includes: str | Iterable[str] | None = None,
    ) -> dict:
        """GET /search/{productNumber}/productdetails."""
        return self._get(
            f"/{_path_segment(product_number)}/productdetails",
            {"manufacturerId": manufacturer_id, "includes": _csv(includes)},
        )

    def manufacturers(self) -> dict:
        """GET /search/manufacturers."""
        return self._get("/manufacturers")

    def categories(self) -> dict:
        """GET /search/categories."""
        return self._get("/categories")

    def category_by_id(self, category_id: int) -> dict:
        """GET /search/categories/{categoryId}."""
        return self._get(f"/categories/{_path_segment(category_id)}")

    def digireel_pricing(self, product_number: str, requested_quantity: int) -> dict:
        """GET /search/{productNumber}/digireelpricing."""
        return self._get(
            f"/{_path_segment(product_number)}/digireelpricing",
            {"requestedQuantity": requested_quantity},
        )

    def recommended_products(
        self,
        product_number: str,
        *,
        limit: int | None = None,
        search_options: str | Iterable[str] | None = None,
        exclude_marketplace: bool | None = None,
    ) -> dict:
        """GET /search/{productNumber}/recommendedproducts."""
        return self._get(
            f"/{_path_segment(product_number)}/recommendedproducts",
            {
                "limit": limit,
                "searchOptionList": _csv(search_options),
                "excludeMarketPlaceProducts": exclude_marketplace,
            },
        )

    def substitutions(self, product_number: str, *, includes: str | Iterable[str] | None = None) -> dict:
        """GET /search/{productNumber}/substitutions."""
        return self._get(
            f"/{_path_segment(product_number)}/substitutions", {"includes": _csv(includes)}
        )

    def associations(self, product_number: str) -> dict:
        """GET /search/{productNumber}/associations."""
        return self._get(f"/{_path_segment(product_number)}/associations")

    def package_type_by_quantity(
        self,
        product_number: str,
        requested_quantity: int,
        *,
        packaging_preference: str | None = None,
    ) -> dict:
        """GET /search/packagetypebyquantity/{productNumber}."""
        return self._get(
            f"/packagetypebyquantity/{_path_segment(product_number)}",
            {"requestedQuantity": requested_quantity, "packagingPreference": packaging_preference},
        )

    def media(self, product_number: str) -> dict:
        """GET /search/{productNumber}/media."""
        return self._get(f"/{_path_segment(product_number)}/media")

    def pricing(
        self,
        product_number: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
        in_stock: bool | None = None,
        exclude_marketplace: bool | None = None,
        exclude_tariff: bool | None = None,
    ) -> dict:
        """GET /search/{productNumber}/pricing."""
        return self._get(
            f"/{_path_segment(product_number)}/pricing",
            {
                "limit": limit,
                "offset": offset,
                "inStock": in_stock,
                "excludeMarketplace": exclude_marketplace,
                "excludeTariff": exclude_tariff,
            },
        )

    def alternate_packaging(self, product_number: str) -> dict:
        """GET /search/{productNumber}/alternatepackaging."""
        return self._get(f"/{_path_segment(product_number)}/alternatepackaging")

    def pricing_by_quantity(
        self,
        product_number: str,
        requested_quantity: int,
        *,
        manufacturer_id: int | str | None = None,
    ) -> dict:
        """GET /search/{productNumber}/pricingbyquantity/{requestedQuantity}."""
        return self._get(
            f"/{_path_segment(product_number)}/pricingbyquantity/{_path_segment(requested_quantity)}",
            {"manufacturerId": manufacturer_id},
        )
