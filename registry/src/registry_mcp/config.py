"""Registry configuration — all config seams in one place (env-driven).

One registry app for the whole marketplace (unlike the per-publisher seller app).
Connection precedence mirrors the seller:
  1. AAMP_REGISTRY_DSN  — explicit Postgres URL (local / BYO / CI).
  2. otherwise          — mint a Lakebase credential at runtime via the Databricks
                          SDK (AAMP_DATABRICKS_PROFILE locally; the app's service
                          principal when deployed — no secrets in the repo).
"""

from __future__ import annotations

from typing import Optional

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AAMP_", env_file=".env", extra="ignore")

    # Registry identity (its own agent card).
    registry_org_id: str = "registry-aamp"
    registry_name: str = "AAMP Agent Registry"

    # Lakebase target (Autoscaling tier: project -> branch -> endpoint).
    registry_instance: str = "aamp-registry"
    registry_branch: str = "production"
    registry_endpoint: str = "primary"
    registry_database: str = "agent_registry"

    # External registries this registry consults for trust verification (SDK clients).
    aamp_registry_url: str = "https://tools.iabtechlab.com/agent-registry"

    # Auth seams.
    databricks_profile: Optional[str] = None     # set locally; unset in-app (SP creds)
    registry_dsn: Optional[str] = None            # explicit DSN override (no minting)

    # MCP/HTTP bind (Databricks Apps inject the port via env).
    host: str = "0.0.0.0"
    port: int = 8000

    @computed_field
    @property
    def registry_endpoint_path(self) -> str:
        """Autoscaling endpoint resource path (projects/<id>/branches/<b>/endpoints/<e>)."""
        return (
            f"projects/{self.registry_instance}/branches/{self.registry_branch}"
            f"/endpoints/{self.registry_endpoint}"
        )

    @computed_field
    @property
    def kv_schema(self) -> str:
        """SDK kv_store schema for the registry (single registry → single schema)."""
        return "kv_registry"


def get_settings() -> Settings:
    return Settings()
