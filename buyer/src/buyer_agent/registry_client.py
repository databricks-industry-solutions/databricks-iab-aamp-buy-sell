"""Registry client — seller discovery + this buyer's trust lookup.

The buyer discovers which seller serves a channel (capability) and looks up its own
registry trust (which caps the identity tier it may present):
  * `discover_sellers(capability)` — sellers advertising that capability, minus
    BLOCKED decoys.
  * `verify_self()` — this buyer's registry trust (→ tier).

Two implementations behind the `RegistryClient` protocol; `get_registry_client`
picks one from config:
  * `HttpRegistryClient` — talks to the live registry app (REST). Sellers carry their
    real URL and channel (`inventory_types`) on their agent card, and trust→tier is the
    registry's authoritative SDK mapping. Used when `AAMP_REGISTRY_URL` is set.
  * `DbRegistryClient` — reads `agent_registry.aamp` (relational) directly. The fallback
    when `AAMP_REGISTRY_URL` is unset; seller URLs come from the `AAMP_SELLER_URLS` map.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from . import db
from .config import Settings


@dataclass(frozen=True)
class SellerRef:
    """A discovered seller: who it is, what channel it serves, its trust, and how
    to reach it."""

    agent_id: str
    name: str
    capability: str
    trust_level: str
    base_url: Optional[str]   # resolved reachable URL
    registry_url: Optional[str]  # the url the registry holds for this seller


@runtime_checkable
class RegistryClient(Protocol):
    def discover_sellers(self, capability: Optional[str] = None) -> list[SellerRef]: ...
    def verify_self(self) -> Optional[dict]: ...


# ===========================================================================
# Live registry app (REST)
# ===========================================================================
class HttpRegistryClient:
    """Discovers sellers and verifies this buyer's trust against the registry app.

    Discovery maps each registered seller's agent card to a `SellerRef`: the card's
    `url` is the reachable seller base (the buyer appends `/mcp/`), and the card's
    `inventory_types` carry the channel the seller serves (e.g. `ctv`, `linear_tv`).
    Verify resolves this buyer by its own URL and returns the registry's authoritative
    `max_access_tier`.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._base = settings.registry_base()

    # ----- auth ----------------------------------------------------------
    def _auth_header(self) -> dict[str, str]:
        token = self.settings.registry_token
        if token is None:  # default: mint the buyer's SP token (same as Model Serving)
            try:
                from .llm import mint_token

                token = mint_token(self.settings)
            except Exception:
                token = ""
        return {"Authorization": f"Bearer {token}"} if token else {}

    # ----- transport (urllib; no extra deps) -----------------------------
    def _request(self, url: str, body: Optional[dict]) -> object:
        data = json.dumps(body).encode() if body is not None else None
        headers = self._auth_header()
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=data, method="POST" if body is not None else "GET", headers=headers
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def _get(self, path: str, params: Optional[dict] = None) -> object:
        url = f"{self._base}{path}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if query:
                url = f"{url}?{query}"
        return self._request(url, None)

    def _post(self, path: str, body: dict) -> object:
        return self._request(f"{self._base}{path}", body)

    # ----- discovery / trust --------------------------------------------
    def discover_sellers(self, capability: Optional[str] = None) -> list[SellerRef]:
        """Sellers registered in the registry that serve `capability`, minus BLOCKED.

        A seller advertises its channel via its agent card's `inventory_types`; until a
        seller populates that field, it matches no capability (discovery returns empty,
        i.e. NO_SELLER) — that's the expected interim state if the seller side hasn't
        shipped the card change yet. A registry error degrades to empty (no crash)."""
        try:
            agents = self._get("/registry/agents", {"agent_type": "seller"}) or []
        except Exception:
            return []
        cap = capability.lower() if capability else None
        sellers: list[SellerRef] = []
        for a in agents:
            card = a.get("agent_card") or {}
            trust = str(a.get("trust_status") or "").lower()
            if trust == "blocked":
                continue  # discovered, then trust-rejected (decoy) — never connect
            inv = [str(t).lower() for t in (card.get("inventory_types") or [])]
            if cap and cap not in inv:
                continue
            url = (card.get("url") or "").rstrip("/") or None
            sellers.append(
                SellerRef(
                    agent_id=a.get("agent_id") or card.get("name") or "",
                    name=card.get("name") or a.get("agent_id") or "",
                    capability=capability or (inv[0] if inv else ""),
                    trust_level=trust.upper(),
                    base_url=url,
                    registry_url=url,
                )
            )
        return sellers

    def verify_self(self) -> Optional[dict]:
        """This buyer's trust as the registry resolves it (by URL): returns
        {trust_status, max_access_tier, is_blocked}. None when no buyer URL is
        configured or the registry is unreachable (→ caller treats as public)."""
        url = self.settings.buyer_identity_url()
        if not url:
            return None
        try:
            return self._post("/registry/verify", {"agent_url": url})  # type: ignore[return-value]
        except Exception:
            return None


# ===========================================================================
# Lakebase fallback (relational reads)
# ===========================================================================
class DbRegistryClient:
    """Reads `agent_registry.aamp` directly (fallback when `registry_url` is unset)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._url_map = settings.seller_url_map()

    def _resolve_url(self, agent_id: str, capabilities: list[str], registry_url: Optional[str]) -> Optional[str]:
        # Prefer an explicit config-map URL (by agent_id, then capability); fall back
        # to the registry's url only if it looks like a real reachable host.
        if agent_id in self._url_map:
            return self._url_map[agent_id]
        for cap in capabilities:
            if cap in self._url_map:
                return self._url_map[cap]
        if registry_url and ".example" not in registry_url:
            return registry_url.rstrip("/")
        return None

    def discover_sellers(self, capability: Optional[str] = None) -> list[SellerRef]:
        rows = db.query(
            db.registry_dsn(self.settings),
            f"SELECT agent_id, name, capabilities, trust_level, url "
            f"FROM {db.qualify(self.settings, 'registry_agents')} WHERE type = %s",
            ("seller",),
        )
        sellers: list[SellerRef] = []
        for r in rows:
            caps = list(r.get("capabilities") or [])
            if (r.get("trust_level") or "").upper() == "BLOCKED":
                continue  # discovered, then trust-rejected (decoy) — never connect
            if capability and capability not in caps:
                continue
            cap = capability or (caps[0] if caps else "")
            sellers.append(
                SellerRef(
                    agent_id=r["agent_id"],
                    name=r["name"],
                    capability=cap,
                    trust_level=r["trust_level"],
                    base_url=self._resolve_url(r["agent_id"], caps, r.get("url")),
                    registry_url=r.get("url"),
                )
            )
        return sellers

    def verify_self(self) -> Optional[dict]:
        """This buyer's registry trust row (trust_status → tier_ceiling)."""
        rows = db.query(
            db.registry_dsn(self.settings),
            f"SELECT agent_id, trust_status, tier_ceiling "
            f"FROM {db.qualify(self.settings, 'registry_trust')} WHERE agent_id = %s",
            (self.settings.buyer_id,),
        )
        return rows[0] if rows else None


def get_registry_client(settings: Settings) -> RegistryClient:
    """Discovery seam: the live registry app when `registry_url` is set, else Lakebase."""
    if settings.registry_url:
        return HttpRegistryClient(settings)
    return DbRegistryClient(settings)
