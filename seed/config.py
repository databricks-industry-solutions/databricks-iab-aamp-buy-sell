"""Load config.yaml (the customer's self-deploy config)."""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULTS = {
    "workspace_profile": "DEFAULT",
    "instance_prefix": "aamp",
    "provision_lakebase": True,
    "dsn": {"seller": "", "buyer": "", "registry": ""},
}

# party -> database name inside its Lakebase instance
PARTY_DB = {"buyer": "ad_buyer", "seller": "ad_seller", "registry": "agent_registry"}


def load_config(path: str | Path = "config.yaml") -> dict:
    p = Path(path)
    cfg = dict(DEFAULTS)
    src = p if p.exists() else Path("config.example.yaml")
    if src.exists():
        cfg.update({k: v for k, v in (yaml.safe_load(src.read_text()) or {}).items() if v is not None})
    return cfg
