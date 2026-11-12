"""Every DigiKey call goes through here: response cache, quota, error mapping."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from ..client import DigiKeyError
from . import adapter
from .models import ResolveError
from .prefs import Preferences
from .store import QuotaExhausted, Store


class NotFound(ResolveError):
    """DigiKey has no such product."""


class ApiError(ResolveError):
    """DigiKey rejected or failed a call."""


class Gateway:
    def __init__(
        self,
        client: Any,
        store: Store,
        prefs: Preferences,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        deadline: float | None = None,
    ):
        self.client = client
        self.store = store
        self.prefs = prefs
        self.clock = clock
        self.sleep = sleep
        #: wall-clock time after which no further waiting is allowed
        self.deadline = deadline
        self.calls_made = 0
        self.cache_hits = 0

    def _ttl(self, endpoint: str) -> float:
        if adapter.ENDPOINT_CLASS.get(endpoint) == "static":
            return self.prefs.static_ttl_days * 86400.0
        return self.prefs.price_ttl_hours * 3600.0

    def _reserve(self) -> None:
        limit_min = max(self.prefs.per_minute - self.prefs.reserve, 1)
        limit_day = max(self.prefs.per_day - self.prefs.reserve, 1)
        while True:
            now = self.clock()
            wait = self.store.reserve_call(now, limit_min, limit_day)
            if wait <= 0:
                return
            if wait > self.prefs.max_sleep_seconds or (self.deadline is not None and now + wait > self.deadline):
                raise QuotaExhausted("minute", wait, f"per-minute limit of {self.prefs.per_minute} calls reached")
            self.sleep(wait)

    def call(self, endpoint: str, **args: Any) -> dict:
        key = endpoint + json.dumps(args, sort_keys=True, default=str)
        now = self.clock()
        cached = self.store.cache_get(key, now)
        if cached is not None:
            self.cache_hits += 1
            if "__not_found" in cached:
                raise NotFound(cached["__not_found"])
            return cached
        self._reserve()
        self.calls_made += 1
        try:
            data = adapter.fetch(self.client, endpoint, args)
        except DigiKeyError as exc:
            status = getattr(exc, "status", None)
            if status == 404:
                self.store.cache_put(key, {"__not_found": str(exc)}, self._ttl(endpoint), self.clock())
                raise NotFound(str(exc)) from None
            if status == 403:
                self.store.block_until(self.clock() + 600.0)
                raise QuotaExhausted("suspended", 600.0, f"DigiKey returned 403 (suspension): {exc}") from None
            if status == 429:
                raise QuotaExhausted("minute", 60.0, f"DigiKey rate limit: {exc}") from None
            raise ApiError(str(exc)) from None
        self.store.cache_put(key, data, self._ttl(endpoint), self.clock())
        return data

    # ---- typed endpoints -------------------------------------------------

    def product_details(self, pn: str) -> adapter.ProductInfo | None:
        return adapter.parse_details(self.call("product_details", pn=pn))

    def search(
        self, keywords: str, limit: int = 10, *, in_stock: bool = False, min_quantity: int | None = None
    ) -> list[adapter.ProductInfo]:
        args: dict[str, Any] = {"keywords": keywords, "limit": limit}
        if in_stock:
            args["search_options"] = ["InStock"]
        if min_quantity:
            args["min_quantity"] = min_quantity
        return adapter.parse_keyword(self.call("keyword_search", **args))

    def substitutions(self, pn: str) -> list[adapter.SubstituteInfo]:
        return adapter.parse_substitutes(self.call("substitutions", pn=pn))

    def alternate_packaging(self, pn: str) -> list[str]:
        return adapter.parse_alternate_packaging(self.call("alternate_packaging", pn=pn))

    def quote(self, pn: str, quantity: int) -> list[adapter.QuoteOption]:
        return adapter.parse_quote(self.call("pricing_by_quantity", pn=pn, quantity=quantity))

    def package_types(self, pn: str, quantity: int) -> list[adapter.Packaging]:
        return adapter.parse_package_types(self.call("package_type_by_quantity", pn=pn, quantity=quantity))
