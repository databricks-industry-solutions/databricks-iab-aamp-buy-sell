"""Seller MCP client — the buyer's outbound leg to a seller app's `/mcp/`.

Each seller app exposes its operations as MCP tools over streamable HTTP. This wraps
one seller connection: connect → discover tools → call them → parse the JSON result.

The seller's MCP tool surface is:
  list_packages, get_package, get_pricing, request_quote, create_order,
  create_line, submit_creative, list_orders
Identity is passed as explicit `seat_id`/`agency_id`/`advertiser_id` tool args (the
seller maps them to a tier).

Trailing slash: connect to `<base>/mcp/`. A bare `/mcp` 307-redirects to `/mcp/` on the
app's internal host (unreachable off-box on Databricks Apps), and the MCP client does
not replay the POST across the redirect — so the path must keep its trailing slash.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Optional

from mcp import ClientSession

try:  # the (read, write, get_session_id) context-manager factory.
    from mcp.client.streamable_http import streamablehttp_client as _streamable_http_client
except ImportError:  # pragma: no cover - only if a future mcp drops the old name
    from mcp.client.streamable_http import streamable_http_client as _streamable_http_client

if TYPE_CHECKING:
    from .registry_client import SellerRef


class SellerMCPError(RuntimeError):
    """A seller MCP tool returned an error, or no reachable seller URL was given."""


def run_sync(coro: Any) -> Any:
    """Run an async coroutine from sync code (CrewAI tools / the deterministic flow).

    If we're already inside a running event loop (some CrewAI executors), run the
    coroutine to completion in a dedicated worker thread with its own loop; otherwise
    just `asyncio.run` it."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


def _text(result: Any) -> Optional[str]:
    parts = [getattr(c, "text", None) for c in (getattr(result, "content", None) or [])]
    parts = [p for p in parts if p is not None]
    return "".join(parts) if parts else None


def _parse(result: Any) -> Any:
    """Normalize a CallToolResult to plain Python.

    FastMCP returns structured output under `structuredContent` (list/scalar returns
    are wrapped as ``{"result": ...}``) and the same value JSON-encoded in a text
    content block. Prefer structured; fall back to parsing the text block."""
    if getattr(result, "isError", False):
        raise SellerMCPError(_text(result) or "seller MCP tool returned an error")
    sc = getattr(result, "structuredContent", None)
    if sc is not None:
        if isinstance(sc, dict) and list(sc.keys()) == ["result"]:
            return sc["result"]
        return sc
    txt = _text(result)
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except (ValueError, TypeError):
        return txt


def _identity(seat_id: Optional[str], agency_id: Optional[str], advertiser_id: Optional[str]) -> dict:
    """Only send the identity fields the buyer actually has (omit None -> anon/public)."""
    pairs = (("seat_id", seat_id), ("agency_id", agency_id), ("advertiser_id", advertiser_id))
    return {k: v for k, v in pairs if v is not None}


class SellerMCPClient:
    """One streamable-HTTP MCP connection to a single seller app.

    Use as an async context manager (preferred — closes the transport cleanly):
        async with SellerMCPClient(base_url) as c:
            pkgs = await c.list_packages(agency_id="agcy-northstar")
    """

    def __init__(
        self,
        base_url: str,
        *,
        mcp_path: str = "/mcp/",
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.mcp_url = f"{base_url.rstrip('/')}/{mcp_path.lstrip('/')}"
        self.headers = headers or None
        self._stack: Optional[AsyncExitStack] = None
        self.session: Optional[ClientSession] = None

    @classmethod
    def for_seller(
        cls,
        seller: "SellerRef",
        *,
        mcp_path: str = "/mcp/",
        headers: Optional[dict[str, str]] = None,
    ) -> "SellerMCPClient":
        """Build a client for a discovered seller. Raises if the registry/config map
        did not resolve a reachable base_url (e.g. AAMP_SELLER_URLS is unset)."""
        if not seller.base_url:
            raise SellerMCPError(
                f"seller {seller.agent_id} ({seller.capability}) has no reachable base_url; "
                f"set AAMP_SELLER_URLS (e.g. '{seller.capability}=https://<seller app url>')"
            )
        return cls(seller.base_url, mcp_path=mcp_path, headers=headers)

    async def connect(self) -> "SellerMCPClient":
        self._stack = AsyncExitStack()
        read, write, _ = await self._stack.enter_async_context(
            _streamable_http_client(self.mcp_url, headers=self.headers)
        )
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self.session = None

    async def __aenter__(self) -> "SellerMCPClient":
        return await self.connect()

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ----- low-level ----------------------------------------------------

    async def list_tools(self) -> list[str]:
        result = await self.session.list_tools()
        return [t.name for t in result.tools]

    async def call(self, name: str, arguments: Optional[dict] = None) -> Any:
        result = await self.session.call_tool(name, arguments or {})
        return _parse(result)

    # ----- typed wrappers (mirror the seller's MCP tool signatures) -----

    async def list_packages(
        self,
        *,
        audience_id: Optional[str] = None,
        audience_type: str = "standard",
        featured_only: bool = False,
        seat_id: Optional[str] = None,
        agency_id: Optional[str] = None,
        advertiser_id: Optional[str] = None,
    ) -> list[dict]:
        args: dict[str, Any] = {"audience_type": audience_type, "featured_only": featured_only}
        if audience_id:
            args["audience_id"] = audience_id
        args.update(_identity(seat_id, agency_id, advertiser_id))
        return await self.call("list_packages", args)

    async def get_package(
        self, package_id: str, *, seat_id=None, agency_id=None, advertiser_id=None
    ) -> Optional[dict]:
        args = {"package_id": package_id, **_identity(seat_id, agency_id, advertiser_id)}
        return await self.call("get_package", args)

    async def get_pricing(
        self, product_id: str, *, volume: int = 0, seat_id=None, agency_id=None, advertiser_id=None
    ) -> Optional[dict]:
        args = {"product_id": product_id, "volume": volume, **_identity(seat_id, agency_id, advertiser_id)}
        return await self.call("get_pricing", args)

    async def request_quote(
        self,
        product_id: str,
        *,
        deal_type: str = "PD",
        impressions: Optional[int] = None,
        flight_start: Optional[str] = None,
        flight_end: Optional[str] = None,
        seat_id=None, agency_id=None, advertiser_id=None,
    ) -> dict:
        args: dict[str, Any] = {"product_id": product_id, "deal_type": deal_type}
        if impressions is not None:
            args["impressions"] = impressions
        if flight_start:
            args["flight_start"] = flight_start
        if flight_end:
            args["flight_end"] = flight_end
        args.update(_identity(seat_id, agency_id, advertiser_id))
        return await self.call("request_quote", args)

    async def create_order(self, *, seat_id=None, agency_id=None, advertiser_id=None) -> dict:
        return await self.call("create_order", _identity(seat_id, agency_id, advertiser_id))

    async def create_line(self, order_id: str, quote_id: str) -> dict:
        return await self.call("create_line", {"order_id": order_id, "quote_id": quote_id})

    async def create_deal(self, quote_id: str, *, notes: Optional[str] = None) -> dict:
        # rail 2, step 1: mint a PG Deal ID from an accepted quote (marks it BOOKED)
        args: dict[str, Any] = {"quote_id": quote_id}
        if notes:
            args["notes"] = notes
        return await self.call("create_deal", args)

    async def distribute_deal_via_ssp(self, deal_id: str, ssp: str) -> dict:
        # rail 2, step 2: activate the Deal ID in an SSP so the buyer's DSP can serve
        return await self.call("distribute_deal_via_ssp", {"deal_id": deal_id, "ssp": ssp})

    async def submit_creative(self, order_id: str, asset_url: str, name: str = "creative") -> dict:
        return await self.call("submit_creative", {"order_id": order_id, "asset_url": asset_url, "name": name})

    async def list_orders(self) -> list[dict]:
        return await self.call("list_orders")
