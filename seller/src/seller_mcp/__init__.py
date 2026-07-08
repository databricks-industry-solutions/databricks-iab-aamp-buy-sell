"""Deterministic AAMP seller MCP server.

Reuses the IAB Tech Lab `seller-agent` SDK's models + engines (MediaKitService,
PricingRulesEngine) verbatim, backed by a Lakebase (Postgres) adapter over the
`ad_seller.aamp` schema that `seed/` populates. No agents — discovery, pricing,
quoting, and booking are deterministic SDK code paths.
"""

__version__ = "0.1.0"
