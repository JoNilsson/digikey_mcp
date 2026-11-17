import asyncio

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from digikey_mcp import server
from digikey_mcp.client import DigiKeyClient

from test_trim import PRODUCT


def _client_with(handler):
    def transport(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 600})
        return handler(request)

    return DigiKeyClient("id", "secret", transport=httpx.MockTransport(transport), sleep=lambda s: None)


@pytest.fixture(autouse=True)
def reset_client():
    yield
    server.set_client(None)


def call(tool, args):
    async def run():
        async with Client(server.mcp) as c:
            return await c.call_tool(tool, args)

    return asyncio.run(run())


def test_import_needs_no_credentials(monkeypatch):
    monkeypatch.delenv("CLIENT_ID", raising=False)
    monkeypatch.delenv("CLIENT_SECRET", raising=False)
    assert server.mcp.name == "DigiKey MCP Server"


def test_product_details_is_trimmed_by_default_and_raw_on_request():
    server.set_client(_client_with(lambda r: httpx.Response(200, json={"Product": PRODUCT})))
    trimmed = call("product_details", {"product_number": "311-10.0KHRCT-ND"}).data
    assert trimmed["manufacturer_part_number"] == "RC0603FR-0710KL"
    raw = call("product_details", {"product_number": "311-10.0KHRCT-ND", "raw": True}).data
    assert raw["Product"]["ManufacturerProductNumber"] == "RC0603FR-0710KL"


def test_api_error_becomes_clean_tool_error():
    server.set_client(_client_with(lambda r: httpx.Response(404, json={"detail": "Product not found"})))
    with pytest.raises(ToolError) as info:
        call("product_details", {"product_number": "NOPE"})
    assert "Product not found" in str(info.value)
    assert "Traceback" not in str(info.value)


def test_missing_credentials_become_tool_error(monkeypatch):
    monkeypatch.delenv("CLIENT_ID", raising=False)
    monkeypatch.delenv("CLIENT_SECRET", raising=False)
    monkeypatch.delenv("CLIENT_ID_FILE", raising=False)
    monkeypatch.delenv("CLIENT_SECRET_FILE", raising=False)
    server.set_client(None)
    with pytest.raises(ToolError, match="CLIENT_ID"):
        call("search_manufacturers", {})


def test_expected_tools_registered():
    async def names():
        async with Client(server.mcp) as c:
            return {t.name for t in await c.list_tools()}

    assert asyncio.run(names()) >= {
        "keyword_search",
        "product_details",
        "pricing_by_quantity",
        "alternate_packaging",
        "substitutions",
        "package_type_by_quantity",
        "product_pricing",
        "digireel_pricing",
        "recommended_products",
        "associations",
        "media",
        "search_manufacturers",
        "search_categories",
        "get_category_by_id",
    }


def _clear_credentials(monkeypatch):
    for name in ("CLIENT_ID", "CLIENT_SECRET", "CLIENT_ID_FILE", "CLIENT_SECRET_FILE"):
        monkeypatch.delenv(name, raising=False)


def test_startup_warns_once_on_stderr_when_credentials_are_missing(monkeypatch, capsys):
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("CLIENT_SECRET_FILE", "/home/you/.config/digikey/client_secret")
    assert server.warn_if_credentials_missing() is True
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert "CLIENT_ID not set" in lines[0]
    assert "CLIENT_SECRET_FILE: file not found" in lines[0]
    assert "README.md section 'Setup'" in lines[0] and "digikey-mcp doctor" in lines[0]


def test_startup_is_silent_when_credentials_resolve(monkeypatch, tmp_path, capsys):
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("CLIENT_ID", "id")
    secret = tmp_path / "client_secret"
    secret.write_text("secret\n", encoding="utf-8")
    monkeypatch.setenv("CLIENT_SECRET_FILE", str(secret))
    assert server.warn_if_credentials_missing() is False
    assert capsys.readouterr().err == ""


def test_main_warns_but_still_serves(monkeypatch, capsys):
    _clear_credentials(monkeypatch)
    ran = []
    monkeypatch.setattr(server.mcp, "run", lambda: ran.append(True))
    server.main()
    assert ran == [True]
    assert "credentials missing" in capsys.readouterr().err


def test_instructions_point_to_doctor():
    assert "digikey-mcp doctor" in server.INSTRUCTIONS
