"""FastMCP server exposing the DigiKey Product Search v4 API.

Tools return trimmed data by default; pass raw=True for the full DigiKey
response. The client is created on first use from the environment, so
importing this module needs no credentials and makes no network calls.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from . import trim
from .cart import register as register_cart
from .resolve import register as register_resolve
from .client import DigiKeyClient, DigiKeyError

logger = logging.getLogger(__name__)

mcp = FastMCP("DigiKey MCP Server")

_client: DigiKeyClient | None = None
_client_lock = threading.Lock()


def get_client() -> DigiKeyClient:
    """Return the shared client, building it from the environment on first use."""
    global _client
    with _client_lock:
        if _client is None:
            _client = DigiKeyClient.from_env()
        return _client


def set_client(client: DigiKeyClient | None) -> None:
    """Replace the shared client (used by tests and embedding code)."""
    global _client
    with _client_lock:
        _client = client


def _tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register `fn` as an MCP tool and turn failures into clean ToolErrors."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except (DigiKeyError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - never leak a traceback to the MCP client
            logger.exception("unexpected error in tool %s", fn.__name__)
            raise ToolError(f"{fn.__name__} failed unexpectedly: {type(exc).__name__}") from None

    return mcp.tool(wrapper)


@_tool
def keyword_search(
    keywords: str,
    limit: int = 10,
    offset: int = 0,
    manufacturer_ids: list[int] | None = None,
    category_ids: list[int] | None = None,
    status_ids: list[int] | None = None,
    packaging_ids: list[int] | None = None,
    search_options: list[str] | None = None,
    sort_field: str | None = None,
    sort_order: str = "Ascending",
    include_parameters: bool = True,
    raw: bool = False,
) -> dict:
    """Search DigiKey by keyword or part number.

    Args:
        keywords: Search terms, MPN or DigiKey part number.
        limit: Results to return, 1 to 50.
        offset: Starting index for paging.
        manufacturer_ids: Restrict to these manufacturer ids (see search_manufacturers).
        category_ids: Restrict to these category ids (see search_categories).
        status_ids: Restrict to these product status ids.
        packaging_ids: Restrict to these packaging type ids.
        search_options: Any of ChipOutpost, Has3DModel, HasCadModel, HasDatasheet,
            HasProductPhoto, InStock, NewProduct, NonRohsCompliant,
            NormallyStocking, RohsCompliant.
        sort_field: None, Packaging, ProductStatus, DigiKeyProductNumber,
            ManufacturerProductNumber, Manufacturer, MinimumQuantity,
            QuantityAvailable, Price, Supplier or PriceManufacturerStandardPackage.
        sort_order: Ascending or Descending.
        include_parameters: Include technical parameters in trimmed results.
        raw: Return the full DigiKey response instead of trimmed data.
    """
    data = get_client().keyword_search(
        keywords,
        limit=limit,
        offset=offset,
        manufacturer_ids=manufacturer_ids,
        category_ids=category_ids,
        status_ids=status_ids,
        packaging_ids=packaging_ids,
        search_options=search_options,
        sort_field=sort_field,
        sort_order=sort_order,
    )
    return data if raw else trim.trim_keyword_response(data, include_parameters)


@_tool
def product_details(
    product_number: str,
    manufacturer_id: int | None = None,
    include_parameters: bool = True,
    raw: bool = False,
) -> dict:
    """Get one product with every packaging variation, price breaks and parameters.

    Works best with a DigiKey part number; an MPN may match an unrelated part.

    Args:
        product_number: DigiKey part number or manufacturer part number.
        manufacturer_id: Disambiguates an MPN shared by several manufacturers.
        include_parameters: Include technical parameters in trimmed output.
        raw: Return the full DigiKey response instead of trimmed data.
    """
    data = get_client().product_details(product_number, manufacturer_id=manufacturer_id)
    return data if raw else trim.trim_product_details(data, include_parameters)


@_tool
def pricing_by_quantity(
    product_number: str,
    requested_quantity: int,
    manufacturer_id: int | None = None,
    raw: bool = False,
) -> dict:
    """Price a quantity of a part across the packaging options DigiKey would use.

    Args:
        product_number: DigiKey part number or manufacturer part number.
        requested_quantity: Quantity to price.
        manufacturer_id: Disambiguates an MPN shared by several manufacturers.
        raw: Return the full DigiKey response instead of trimmed data.
    """
    data = get_client().pricing_by_quantity(
        product_number, requested_quantity, manufacturer_id=manufacturer_id
    )
    return data if raw else trim.trim_pricing_by_quantity(data)


@_tool
def alternate_packaging(product_number: str, raw: bool = False) -> dict:
    """List the other packaging variants (cut tape, reel, tube, ...) of a part.

    Args:
        product_number: DigiKey part number.
        raw: Return the full DigiKey response instead of trimmed data.
    """
    data = get_client().alternate_packaging(product_number)
    return data if raw else trim.trim_alternate_packaging(data)


@_tool
def substitutions(product_number: str, raw: bool = False) -> dict:
    """List substitute products DigiKey suggests for a part.

    Args:
        product_number: DigiKey part number.
        raw: Return the full DigiKey response instead of trimmed data.
    """
    data = get_client().substitutions(product_number)
    return data if raw else trim.trim_substitutions(data)


@_tool
def package_type_by_quantity(
    product_number: str,
    requested_quantity: int,
    packaging_preference: str | None = None,
    raw: bool = False,
) -> dict:
    """Recommend the packaging type for buying a given quantity.

    Args:
        product_number: DigiKey part number.
        requested_quantity: Quantity to buy.
        packaging_preference: Preferred packaging for the remainder after full reels.
        raw: Return the full DigiKey response instead of trimmed data.
    """
    data = get_client().package_type_by_quantity(
        product_number, requested_quantity, packaging_preference=packaging_preference
    )
    return data if raw else trim.trim_package_type_by_quantity(data)


@_tool
def product_pricing(
    product_number: str,
    limit: int | None = None,
    offset: int | None = None,
    in_stock: bool | None = None,
    exclude_marketplace: bool | None = None,
    exclude_tariff: bool | None = None,
    raw: bool = False,
) -> dict:
    """Get pricing for every product matching a part number (limit at most 10).

    Args:
        product_number: DigiKey part number or manufacturer part number.
        limit: Products to return, at most 10 (DigiKey default 5).
        offset: Starting index when paging.
        in_stock: Only in-stock products.
        exclude_marketplace: Exclude marketplace products.
        exclude_tariff: Exclude tariff-affected products.
        raw: Return the full DigiKey response instead of trimmed data.
    """
    data = get_client().pricing(
        product_number,
        limit=limit,
        offset=offset,
        in_stock=in_stock,
        exclude_marketplace=exclude_marketplace,
        exclude_tariff=exclude_tariff,
    )
    return data if raw else trim.trim_pricing(data)


@_tool
def digireel_pricing(product_number: str, requested_quantity: int) -> dict:
    """Get DigiReel pricing (reeling fee, unit and extended price) for a quantity.

    Args:
        product_number: DigiKey part number of a DigiReel-capable product.
        requested_quantity: Quantity to price.
    """
    return get_client().digireel_pricing(product_number, requested_quantity)


@_tool
def recommended_products(
    product_number: str,
    limit: int | None = None,
    search_options: list[str] | None = None,
    exclude_marketplace: bool | None = None,
) -> dict:
    """Get products DigiKey recommends alongside a part.

    Args:
        product_number: DigiKey part number.
        limit: Maximum recommendations.
        search_options: Filters such as InStock or RohsCompliant.
        exclude_marketplace: Exclude marketplace products.
    """
    return get_client().recommended_products(
        product_number,
        limit=limit,
        search_options=search_options,
        exclude_marketplace=exclude_marketplace,
    )


@_tool
def associations(product_number: str) -> dict:
    """Get kits, mating products, associated products and for-use-with products.

    Args:
        product_number: DigiKey part number.
    """
    return get_client().associations(product_number)


@_tool
def media(product_number: str) -> dict:
    """Get images, datasheets and videos for a product.

    Args:
        product_number: DigiKey part number.
    """
    return get_client().media(product_number)


@_tool
def search_manufacturers() -> dict:
    """List all manufacturers with their ids."""
    return get_client().manufacturers()


@_tool
def search_categories() -> dict:
    """List all product categories with their ids."""
    return get_client().categories()


@_tool
def get_category_by_id(category_id: int) -> dict:
    """Get one category, with its children, by id.

    Args:
        category_id: Category id.
    """
    return get_client().category_by_id(category_id)


register_cart(mcp)
register_resolve(mcp)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    mcp.run()


if __name__ == "__main__":
    main()
