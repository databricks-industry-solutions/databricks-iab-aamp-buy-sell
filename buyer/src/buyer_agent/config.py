"""Buyer configuration — all env-driven settings in one place (prefix `AAMP_`).

Connection precedence:
  1. AAMP_BUYER_DSN  — explicit Postgres URL (local Postgres / BYO / CI).
  2. otherwise       — mint a Lakebase credential at runtime via the Databricks
                       SDK (AAMP_DATABRICKS_PROFILE locally; the app's service
                       principal when deployed — no secrets in the repo).
"""

from __future__ import annotations

from typing import Optional

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AAMP_", env_file=".env", extra="ignore")

    # The hero buyer this app speaks for (row key in ad_buyer.aamp.*).
    buyer_id: str = "buyer-northstar"

    # Lakebase target (Autoscaling tier: project -> branch -> endpoint).
    # `buyer_instance` is the PROJECT id; the endpoint path is derived from it.
    buyer_instance: str = "aamp-buyer"
    buyer_branch: str = "production"
    buyer_endpoint: str = "primary"
    buyer_database: str = "ad_buyer"
    schema_name: str = "aamp"        # relational schema (briefs / buyer_identities)

    # Registry target — a separate Lakebase instance the buyer reads for seller
    # discovery (agent_registry.aamp), same `aamp` schema. The app's SP needs read
    # access to this instance too. Used only by the Lakebase fallback (DbRegistryClient),
    # i.e. when `registry_url` is unset.
    registry_instance: str = "aamp-registry"
    registry_branch: str = "production"
    registry_endpoint: str = "primary"
    registry_database: str = "agent_registry"

    # Registry app (REST + MCP). When set, the buyer discovers sellers and verifies its
    # own trust against the live registry app — real seller URLs from each seller's agent
    # card and SDK trust→tier — instead of reading Lakebase directly. When unset, it falls
    # back to DbRegistryClient (the relational tables).
    registry_url: Optional[str] = None
    # This buyer's own public agent URL — presented to /registry/verify (the registry
    # resolves trust by URL) on the no-tier fallback path. Falls back to the Databricks
    # Apps-injected URL. The demo normally selects the identity tier per run instead.
    buyer_url: Optional[str] = None
    # Bearer for registry-app calls. Left unset, the buyer mints its SP token (same as
    # Model Serving). Set to "" to force unauthenticated (e.g. a local registry).
    registry_token: Optional[str] = None

    # Auth seams.
    databricks_profile: Optional[str] = None     # set locally; unset in-app (SP creds)
    buyer_dsn: Optional[str] = None               # explicit DSN override (no minting)
    registry_dsn: Optional[str] = None            # explicit registry DSN override (no minting)

    # HTTP bind (Databricks Apps inject the port via env).
    host: str = "0.0.0.0"
    port: int = 8000

    # Per-channel seller base-URL map, e.g. "ctv=https://aamp-seller-summit...,linear_tv=https://...".
    # The registry gives which seller + trust; this supplies the reachable URL, falling
    # back to registry_agents.url when that holds a real (non-placeholder) host.
    seller_urls: str = ""
    # Streamable-HTTP MCP endpoint path on each seller app. The trailing slash matters:
    # the seller mounts FastMCP at /mcp and a bare /mcp 307-redirects to /mcp/ using the
    # app's internal host (unreachable off-box on Databricks Apps), and the MCP client
    # does not replay the POST across that redirect — so always target "/mcp/".
    seller_mcp_path: str = "/mcp/"
    # SSP the buyer asks the seller to distribute PG deals to (Rail 2). The seller-side
    # SSP is a demo stub, so the name is cosmetic — but it appears on every deal artifact
    # (distribution ssp_name, ssp_deal_id prefix, activation copy). Magnite is a real SSP
    # (FreeWheel, the previous default, is an ad server — wrong role). Env: AAMP_DEFAULT_SSP.
    default_ssp: str = "Magnite"
    # Bearer token presented to seller MCP servers. Left unset (None), the buyer mints
    # its SP token (same as the registry + Model Serving) so the seller app's Databricks
    # Apps OAuth proxy authorizes the call — the SP needs CAN_USE on each seller app.
    # Set to "" to force unauthenticated (e.g. a local seller with auth disabled).
    seller_mcp_token: Optional[str] = None
    # CrewAI -> Databricks Model Serving (OpenAI-compatible). Token is the app SP at runtime.
    # `openai_base_url` is optional: if unset it is derived as "<workspace host>/serving-endpoints"
    # from the Databricks SDK config. `llm_model` is the worker/specialist endpoint; the
    # hierarchical manager (L1) can use a stronger endpoint (e.g. an opus) via manager_llm_model.
    openai_base_url: Optional[str] = None
    llm_model: str = "databricks-claude-sonnet-4-5"
    manager_llm_model: str = "databricks-claude-sonnet-4-5"  # bump to databricks-claude-opus-4-8 for a stronger L1 manager

    # MLflow tracing of the L1/L2/L3 run (mlflow.crewai.autolog()). Optional: unset
    # disables tracing (run still works). On Databricks set tracking_uri="databricks"
    # and an experiment path; locally point at a file/SQLite store or leave unset.
    mlflow_tracking_uri: Optional[str] = None
    mlflow_experiment: Optional[str] = None

    @computed_field
    @property
    def buyer_endpoint_path(self) -> str:
        """Autoscaling endpoint resource path (projects/<id>/branches/<b>/endpoints/<e>).
        This is what w.postgres uses to mint a credential and resolve the host."""
        return (
            f"projects/{self.buyer_instance}/branches/{self.buyer_branch}"
            f"/endpoints/{self.buyer_endpoint}"
        )

    @computed_field
    @property
    def registry_endpoint_path(self) -> str:
        """Autoscaling endpoint resource path for the registry instance."""
        return (
            f"projects/{self.registry_instance}/branches/{self.registry_branch}"
            f"/endpoints/{self.registry_endpoint}"
        )

    def seller_auth_token(self) -> Optional[str]:
        """Bearer for seller MCP calls. Explicit `seller_mcp_token` wins; when unset
        (None) the buyer mints its SP token (same as the registry + Model Serving) so the
        seller app's OAuth proxy authorizes the call. "" forces unauthenticated."""
        if self.seller_mcp_token is not None:
            return self.seller_mcp_token or None
        try:
            from .llm import mint_token

            return mint_token(self)
        except Exception:
            return None

    def seller_mcp_headers(self) -> dict[str, str]:
        """Auth headers for the seller MCP connection (SP-minted token unless disabled)."""
        token = self.seller_auth_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def registry_base(self) -> Optional[str]:
        """The registry app's base URL (without trailing slash), or None when unset."""
        return self.registry_url.rstrip("/") if self.registry_url else None

    def buyer_identity_url(self) -> Optional[str]:
        """This buyer's public URL for registry verify-by-URL: explicit `buyer_url`,
        else the Databricks Apps-injected app URL."""
        import os

        for v in (self.buyer_url, os.environ.get("AAMP_PUBLIC_URL"), os.environ.get("DATABRICKS_APP_URL")):
            if v:
                return v.rstrip("/")
        return None

    def seller_url_map(self) -> dict[str, str]:
        """Parse AAMP_SELLER_URLS ("cap=url,cap=url") into {capability: base_url}."""
        out: dict[str, str] = {}
        for pair in self.seller_urls.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            cap, url = pair.split("=", 1)
            out[cap.strip()] = url.strip().rstrip("/")
        return out


def get_settings() -> Settings:
    return Settings()
