"""Seller configuration — all config seams in one place (env-driven).

One seller app per publisher, parameterized by AAMP_SELLER (the kv schema slug,
e.g. "summit" | "beacon"). The DAB sets it per app.yaml so the same image deploys
N sellers.

Connection precedence:
  1. AAMP_SELLER_DSN  — explicit Postgres URL (local Postgres / BYO / CI).
  2. otherwise        — mint a Lakebase credential at runtime via the Databricks
                        SDK (AAMP_DATABRICKS_PROFILE locally; the app's service
                        principal when deployed — no secrets in the repo).
"""

from __future__ import annotations

from typing import Optional

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Channel(s) each seller slug serves — advertised on its A2A agent card as
# `inventory_types` (lowercase, matching the buyer's capability ids). The default when
# AAMP_INVENTORY_TYPES is unset; a real publisher would derive this from its catalog.
_DEFAULT_INVENTORY: dict[str, list[str]] = {
    "summit": ["ctv"],
    "beacon": ["linear_tv"],
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AAMP_", env_file=".env", extra="ignore")

    # The publisher this app speaks for. `seller` is the kv-schema slug (AAMP_SELLER);
    # seller_org_id is the row key in aamp.* (defaults to "seller-<slug>").
    seller: str = "summit"
    seller_org_id: Optional[str] = None
    # Human-readable publisher name for the agent card (e.g. "Beacon Broadcast"); stamped
    # per slug by render_apps. Falls back to the org id when unset.
    seller_display_name: Optional[str] = None

    # Channels advertised on the agent card (A2A `inventory_types`). Comma-separated,
    # lowercase; falls back to the per-slug default when unset.
    inventory_types: str = ""

    # Lakebase target (Autoscaling tier: project -> branch -> endpoint).
    # `seller_instance` is the PROJECT id; the endpoint path is derived from it.
    seller_instance: str = "aamp-seller"
    seller_branch: str = "production"
    seller_endpoint: str = "primary"
    seller_database: str = "ad_seller"
    schema_name: str = "aamp"        # relational schema (pricing_rules etc.)

    # Auth seams.
    databricks_profile: Optional[str] = None     # set locally; unset in-app (SP creds)
    seller_dsn: Optional[str] = None              # explicit DSN override (no minting)

    # MCP/HTTP bind (Databricks Apps inject the port via env).
    host: str = "0.0.0.0"
    port: int = 8000

    @computed_field
    @property
    def seller_endpoint_path(self) -> str:
        """Autoscaling endpoint resource path (projects/<id>/branches/<b>/endpoints/<e>).
        This is what w.postgres uses to mint a credential and resolve the host."""
        return (
            f"projects/{self.seller_instance}/branches/{self.seller_branch}"
            f"/endpoints/{self.seller_endpoint}"
        )

    @computed_field
    @property
    def kv_schema(self) -> str:
        """Per-seller SDK kv_store schema (kv_<slug>)."""
        return f"kv_{self.seller}"

    @computed_field
    @property
    def org_id(self) -> str:
        """Seller org id used as the relational partition key in aamp.*."""
        return self.seller_org_id or f"seller-{self.seller}"

    @computed_field
    @property
    def display_name(self) -> str:
        """Human-readable publisher name for the agent card; falls back to the org id."""
        return self.seller_display_name or self.org_id

    @computed_field
    @property
    def inventory_type_list(self) -> list[str]:
        """Channels for the agent card: AAMP_INVENTORY_TYPES if set, else the per-slug
        default. Lowercase so buyers can match them to a campaign's channel."""
        if self.inventory_types.strip():
            return [t.strip().lower() for t in self.inventory_types.split(",") if t.strip()]
        return _DEFAULT_INVENTORY.get(self.seller, [])


def get_settings() -> Settings:
    return Settings()
