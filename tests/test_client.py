import json
import logging

import httpx
import pytest

from digikey_mcp.client import (
    DigiKeyAuthError,
    DigiKeyClient,
    DigiKeyConfigError,
    DigiKeyError,
    load_credentials,
)

SECRET = "s3cr3t-value"
TOKEN = "tok-abc-123"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Api:
    """Scripted DigiKey: records requests, answers token and API calls."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.api_responses: list[httpx.Response] = []
        self.token_count = 0
        self.expires_in = 600

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            self.token_count += 1
            return httpx.Response(
                200, json={"access_token": f"{TOKEN}-{self.token_count}", "expires_in": self.expires_in}
            )
        if self.api_responses:
            return self.api_responses.pop(0)
        return httpx.Response(200, json={"ok": True})

    @property
    def api_requests(self):
        return [r for r in self.requests if r.url.path != "/v1/oauth2/token"]


@pytest.fixture
def api():
    return Api()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def sleeps():
    return []


def make(api, clock, sleeps, **kw):
    return DigiKeyClient(
        "client-id",
        SECRET,
        transport=httpx.MockTransport(api),
        clock=clock,
        sleep=sleeps.append,
        **kw,
    )


# ---- credentials and configuration ----------------------------------------


def test_load_credentials_from_env():
    assert load_credentials({"CLIENT_ID": "a", "CLIENT_SECRET": "b"}) == ("a", "b")


def test_load_credentials_from_files(tmp_path):
    (tmp_path / "id").write_text("file-id\n")
    (tmp_path / "secret").write_text("file-secret\n")
    env = {
        "CLIENT_ID_FILE": str(tmp_path / "id"),
        "CLIENT_SECRET_FILE": str(tmp_path / "secret"),
        "CLIENT_ID": "ignored",
    }
    assert load_credentials(env) == ("file-id", "file-secret")


def test_load_credentials_missing_and_unreadable(tmp_path):
    with pytest.raises(DigiKeyConfigError, match="CLIENT_ID and CLIENT_SECRET"):
        load_credentials({})
    with pytest.raises(DigiKeyConfigError, match="CLIENT_ID_FILE"):
        load_credentials({"CLIENT_ID_FILE": str(tmp_path / "nope"), "CLIENT_SECRET": "x"})


@pytest.mark.parametrize(
    "value,base",
    [
        ("true", "https://sandbox-api.digikey.com"),
        ("TRUE", "https://sandbox-api.digikey.com"),
        ("false", "https://api.digikey.com"),
        (None, "https://api.digikey.com"),
    ],
)
def test_sandbox_flag(value, base):
    env = {"CLIENT_ID": "a", "CLIENT_SECRET": "b"}
    if value is not None:
        env["DIGIKEY_SANDBOX"] = value
    client = DigiKeyClient.from_env(env)
    assert client.base_url == base
    assert client.sandbox == (base != "https://api.digikey.com")


def test_from_env_locale_and_overrides():
    env = {
        "CLIENT_ID": "a",
        "CLIENT_SECRET": "b",
        "DIGIKEY_LOCALE_SITE": "CA",
        "DIGIKEY_LOCALE_CURRENCY": "CAD",
        "DIGIKEY_CUSTOMER_ID": "42",
    }
    client = DigiKeyClient.from_env(env, locale_language="fr")
    assert client._locale == {
        "X-DIGIKEY-Locale-Site": "CA",
        "X-DIGIKEY-Locale-Language": "fr",
        "X-DIGIKEY-Locale-Currency": "CAD",
    }
    assert client._customer_id == "42"


def test_sandbox_token_url(api, clock, sleeps):
    client = make(api, clock, sleeps, sandbox=True)
    client.manufacturers()
    assert str(api.requests[0].url) == "https://sandbox-api.digikey.com/v1/oauth2/token"
    assert api.api_requests[0].url.host == "sandbox-api.digikey.com"


# ---- headers and token handling -------------------------------------------


def test_headers_and_token_request(api, clock, sleeps):
    client = make(api, clock, sleeps, locale_site="DE", locale_language="de", locale_currency="EUR")
    client.manufacturers()
    token_req, req = api.requests
    body = dict(p.split("=") for p in token_req.content.decode().split("&"))
    assert body["grant_type"] == "client_credentials"
    assert body["client_id"] == "client-id"
    assert req.headers["Authorization"] == f"Bearer {TOKEN}-1"
    assert req.headers["X-DIGIKEY-Client-Id"] == "client-id"
    assert req.headers["X-DIGIKEY-Locale-Site"] == "DE"
    assert req.headers["X-DIGIKEY-Locale-Language"] == "de"
    assert req.headers["X-DIGIKEY-Locale-Currency"] == "EUR"
    assert "X-DIGIKEY-Customer-Id" not in req.headers


def test_customer_id_header(api, clock, sleeps):
    make(api, clock, sleeps, customer_id="99").manufacturers()
    assert api.api_requests[0].headers["X-DIGIKEY-Customer-Id"] == "99"


def test_token_cached_then_refreshed_before_expiry(api, clock, sleeps):
    client = make(api, clock, sleeps, cache_ttl=0, token_margin=60)
    api.expires_in = 600
    client.manufacturers()
    client.manufacturers()
    assert api.token_count == 1
    clock.now += 539  # still inside lifetime minus margin
    client.manufacturers()
    assert api.token_count == 1
    clock.now += 2  # past 540 s
    client.manufacturers()
    assert api.token_count == 2
    assert api.api_requests[-1].headers["Authorization"] == f"Bearer {TOKEN}-2"


def test_401_refreshes_token_and_retries_once(api, clock, sleeps):
    api.api_responses = [httpx.Response(401, json={"title": "expired"}), httpx.Response(200, json={"ok": 1})]
    client = make(api, clock, sleeps)
    assert client.manufacturers() == {"ok": 1}
    assert api.token_count == 2
    assert [r.headers["Authorization"] for r in api.api_requests] == [f"Bearer {TOKEN}-1", f"Bearer {TOKEN}-2"]


def test_persistent_401_raises_after_single_retry(api, clock, sleeps):
    api.api_responses = [httpx.Response(401, json={"title": "no"})] * 3
    client = make(api, clock, sleeps)
    with pytest.raises(DigiKeyAuthError) as info:
        client.manufacturers()
    assert info.value.status == 401
    assert len(api.api_requests) == 2


def test_token_request_rejected(clock, sleeps):
    def handler(request):
        return httpx.Response(401, json={"ErrorMessage": "Invalid client credentials"})

    client = DigiKeyClient(
        "client-id", SECRET, transport=httpx.MockTransport(handler), clock=clock, sleep=sleeps.append
    )
    with pytest.raises(DigiKeyAuthError, match="Invalid client credentials") as info:
        client.manufacturers()
    assert SECRET not in str(info.value)


# ---- rate limiting and caching --------------------------------------------


def test_429_honours_retry_after_then_succeeds(api, clock, sleeps):
    api.api_responses = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"ok": 1}),
    ]
    assert make(api, clock, sleeps).manufacturers() == {"ok": 1}
    assert sleeps == [7.0, 3.0]


def test_429_is_bounded_and_wait_is_capped(api, clock, sleeps):
    api.api_responses = [httpx.Response(429, headers={"Retry-After": "9999"})] * 10
    client = make(api, clock, sleeps, max_retries=2, max_retry_wait=30)
    with pytest.raises(DigiKeyError, match="rate limit") as info:
        client.manufacturers()
    assert info.value.status == 429
    assert sleeps == [30.0, 30.0]
    assert len(api.api_requests) == 3


def test_429_without_retry_after_backs_off_exponentially(api, clock, sleeps):
    api.api_responses = [httpx.Response(429), httpx.Response(429), httpx.Response(200, json={})]
    make(api, clock, sleeps).manufacturers()
    assert sleeps == [1.0, 2.0]


def test_get_cache_hit_and_expiry(api, clock, sleeps):
    client = make(api, clock, sleeps, cache_ttl=60)
    first = client.product_details("X1")
    first["mutated"] = True
    assert client.product_details("X1") == {"ok": True}
    assert len(api.api_requests) == 1
    client.product_details("X2")
    assert len(api.api_requests) == 2
    clock.now += 61
    client.product_details("X1")
    assert len(api.api_requests) == 3


def test_post_is_not_cached(api, clock, sleeps):
    client = make(api, clock, sleeps)
    client.keyword_search("lm358")
    client.keyword_search("lm358")
    assert len(api.api_requests) == 2


def test_errors_are_not_cached(api, clock, sleeps):
    api.api_responses = [httpx.Response(404, json={"detail": "Product not found"})]
    client = make(api, clock, sleeps)
    with pytest.raises(DigiKeyError, match="Product not found") as info:
        client.product_details("NOPE")
    assert info.value.status == 404
    assert client.product_details("NOPE") == {"ok": True}


def test_error_message_uses_problem_details(api, clock, sleeps):
    api.api_responses = [
        httpx.Response(400, json={"title": "Bad", "detail": "limit too big", "correlationId": "cid-1"})
    ]
    with pytest.raises(DigiKeyError) as info:
        make(api, clock, sleeps).manufacturers()
    assert "limit too big" in str(info.value)
    assert info.value.correlation_id == "cid-1"


def test_transport_failure_is_clean(clock, sleeps):
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 600})
        raise httpx.ConnectError(f"boom {SECRET}")

    client = DigiKeyClient(
        "client-id", SECRET, transport=httpx.MockTransport(handler), clock=clock, sleep=sleeps.append
    )
    with pytest.raises(DigiKeyError) as info:
        client.manufacturers()
    assert SECRET not in str(info.value)
    assert "ConnectError" in str(info.value)


def test_no_secrets_in_logs(api, clock, sleeps, caplog):
    api.api_responses = [httpx.Response(429, headers={"Retry-After": "1"})]
    with caplog.at_level(logging.DEBUG):
        make(api, clock, sleeps).manufacturers()
    assert SECRET not in caplog.text
    assert TOKEN not in caplog.text


# ---- endpoints -------------------------------------------------------------


def test_path_segments_are_encoded(api, clock, sleeps):
    client = make(api, clock, sleeps)
    client.product_details("AB/12 #3?x")
    raw = api.api_requests[0].url.raw_path.decode()
    assert raw == "/products/v4/search/AB%2F12%20%233%3Fx/productdetails"


def test_keyword_search_body(api, clock, sleeps):
    make(api, clock, sleeps).keyword_search(
        "10k 0603",
        limit=5,
        offset=10,
        manufacturer_ids=[1, 2],
        category_ids=["52"],
        search_options=["InStock", "RohsCompliant"],
        minimum_quantity_available=100,
        sort_field="Price",
        sort_order="Descending",
    )
    req = api.api_requests[0]
    assert req.method == "POST"
    assert req.url.path == "/products/v4/search/keyword"
    assert req.headers["Content-Type"] == "application/json"
    assert json.loads(req.content) == {
        "Keywords": "10k 0603",
        "Limit": 5,
        "Offset": 10,
        "FilterOptionsRequest": {
            "ManufacturerFilter": [{"Id": "1"}, {"Id": "2"}],
            "CategoryFilter": [{"Id": "52"}],
            "SearchOptions": ["InStock", "RohsCompliant"],
            "MinimumQuantityAvailable": 100,
        },
        "SortOptions": {"Field": "Price", "SortOrder": "Descending"},
    }


def test_keyword_search_parameter_filters(api, clock, sleeps):
    client = make(api, clock, sleeps)
    client.keyword_search(
        "resistor", parameter_category_id=52, parameter_filters={2085: ["10 kOhms"], 16: ["39329", "39328"], 3: []},
        marketplace_filter="ExcludeMarketPlace",
    )
    client.keyword_search("resistor", parameter_category_id="52")
    first, second = (json.loads(r.content)["FilterOptionsRequest"] for r in api.api_requests)
    assert first == {
        "MarketPlaceFilter": "ExcludeMarketPlace",
        "ParameterFilterRequest": {
            "CategoryFilter": {"Id": "52"},
            "ParameterFilters": [
                {"ParameterId": 2085, "FilterValues": [{"Id": "10 kOhms"}]},
                {"ParameterId": 16, "FilterValues": [{"Id": "39329"}, {"Id": "39328"}]},
            ],
        },
    }
    assert second == {"ParameterFilterRequest": {"CategoryFilter": {"Id": "52"}}}


def test_keyword_search_validates_input(api, clock, sleeps):
    client = make(api, clock, sleeps)
    with pytest.raises(ValueError):
        client.keyword_search("  ")
    with pytest.raises(ValueError):
        client.keyword_search("x", limit=51)
    assert api.requests == []


ENDPOINTS = [
    (lambda c: c.keyword_search("x"), "POST", "/keyword", None),
    (lambda c: c.product_details("P", manufacturer_id=7), "GET", "/P/productdetails", {"manufacturerId": "7"}),
    (lambda c: c.manufacturers(), "GET", "/manufacturers", None),
    (lambda c: c.categories(), "GET", "/categories", None),
    (lambda c: c.category_by_id(12), "GET", "/categories/12", None),
    (lambda c: c.digireel_pricing("P", 500), "GET", "/P/digireelpricing", {"requestedQuantity": "500"}),
    (
        lambda c: c.recommended_products("P", limit=3, search_options=["InStock", "HasDatasheet"], exclude_marketplace=True),
        "GET",
        "/P/recommendedproducts",
        {"limit": "3", "searchOptionList": "InStock,HasDatasheet", "excludeMarketPlaceProducts": "true"},
    ),
    (lambda c: c.substitutions("P"), "GET", "/P/substitutions", None),
    (lambda c: c.associations("P"), "GET", "/P/associations", None),
    (
        lambda c: c.package_type_by_quantity("P", 250, packaging_preference="CutTape"),
        "GET",
        "/packagetypebyquantity/P",
        {"requestedQuantity": "250", "packagingPreference": "CutTape"},
    ),
    (lambda c: c.media("P"), "GET", "/P/media", None),
    (
        lambda c: c.pricing("P", limit=5, in_stock=True, exclude_tariff=False),
        "GET",
        "/P/pricing",
        {"limit": "5", "inStock": "true", "excludeTariff": "false"},
    ),
    (lambda c: c.alternate_packaging("P"), "GET", "/P/alternatepackaging", None),
    (
        lambda c: c.pricing_by_quantity("P", 1000, manufacturer_id=3),
        "GET",
        "/P/pricingbyquantity/1000",
        {"manufacturerId": "3"},
    ),
]


@pytest.mark.parametrize("call,method,path,query", ENDPOINTS)
def test_endpoint_routes(api, clock, sleeps, call, method, path, query):
    assert call(make(api, clock, sleeps)) == {"ok": True}
    req = api.api_requests[0]
    assert req.method == method
    assert req.url.path == f"/products/v4/search{path}"
    assert dict(req.url.params) == (query or {})


def test_all_fourteen_endpoints_covered():
    assert len(ENDPOINTS) == 14
