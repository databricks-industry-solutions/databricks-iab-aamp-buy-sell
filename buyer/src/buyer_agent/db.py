"""Lakebase connections + relational reads of `ad_buyer.aamp` and `agent_registry.aamp`.

Connect with a BYO DSN, or mint a short-lived Lakebase OAuth credential via the
Databricks SDK (the caller's identity locally, the app's service principal when
deployed). The buyer reads two separate Lakebase instances:
  * the buyer instance (`ad_buyer`) — its bound DB; the runtime may inject
    PGHOST/ENDPOINT_NAME/PGUSER, so we honor those (use_env=True).
  * the registry instance (`agent_registry`) — a second instance the SP is granted;
    not the bound DB, so we always resolve host/user via the SDK (use_env=False).

All reads are one-shot psycopg connections (no pool), tolerating a scaled-to-zero
instance (the connect waits while it wakes).
"""

from __future__ import annotations

import os
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row

from .config import Settings


# ----- credential minting (parameterized by endpoint) -----------------------

def _client(settings: Settings):
    from databricks.sdk import WorkspaceClient

    return (
        WorkspaceClient(profile=settings.databricks_profile)
        if settings.databricks_profile
        else WorkspaceClient()
    )


def _conn_params(settings: Settings, *, endpoint_path: str, database: str, use_env: bool) -> dict:
    """Resolve host/user/port/db + mint a token for an Autoscaling endpoint.

    `use_env=True` honors the runtime-injected PGHOST/ENDPOINT_NAME/PGUSER/PGDATABASE
    (the app's bound buyer instance); `use_env=False` always resolves via the SDK
    (the registry instance, which is not the bound DB)."""
    w = _client(settings)
    ep = (os.environ.get("ENDPOINT_NAME", endpoint_path) if use_env else endpoint_path)
    host = (os.environ.get("PGHOST") if use_env else None)
    if not host:
        host = w.postgres.get_endpoint(name=ep).status.hosts.host
    user = (os.environ.get("PGUSER") if use_env else None) or w.current_user.me().user_name
    token = w.postgres.generate_database_credential(endpoint=ep).token
    dbname = (os.environ.get("PGDATABASE") if use_env else None) or database
    return {
        "host": host,
        "user": user,
        "port": os.environ.get("PGPORT", "5432") if use_env else "5432",
        "dbname": dbname,
        "token": token,
    }


def _dsn(settings: Settings, *, endpoint_path: str, database: str, dsn_override: str | None, use_env: bool) -> str:
    if dsn_override:
        return dsn_override
    p = _conn_params(settings, endpoint_path=endpoint_path, database=database, use_env=use_env)
    return (
        f"postgresql://{quote(p['user'], safe='')}:{quote(p['token'], safe='')}"
        f"@{p['host']}:{p['port']}/{p['dbname']}?sslmode=require"
    )


def buyer_dsn(settings: Settings) -> str:
    """Postgres URL for the buyer DB (bound instance; runtime env shortcuts honored)."""
    return _dsn(
        settings,
        endpoint_path=settings.buyer_endpoint_path,
        database=settings.buyer_database,
        dsn_override=settings.buyer_dsn,
        use_env=True,
    )


def registry_dsn(settings: Settings) -> str:
    """Postgres URL for the registry DB (second instance; resolved via the SDK)."""
    return _dsn(
        settings,
        endpoint_path=settings.registry_endpoint_path,
        database=settings.registry_database,
        dsn_override=settings.registry_dsn,
        use_env=False,
    )


# ----- generic read helpers -------------------------------------------------

def query(dsn: str, sql: str, params: tuple = ()) -> list[dict]:
    """One-shot psycopg read. JSONB → dict, arrays → list (psycopg default adapters)."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def qualify(settings: Settings, table: str) -> str:
    """Schema-qualified table name (the `aamp` schema is shared across instances)."""
    return f'"{settings.schema_name}".{table}'


# ----- ad_buyer.aamp reads --------------------------------------------------

def list_briefs(settings: Settings) -> list[dict]:
    """All briefs (the hero buyer's campaign briefs)."""
    return query(buyer_dsn(settings), f"SELECT * FROM {qualify(settings, 'briefs')} ORDER BY brief_id")


def get_brief(settings: Settings, brief_id: str) -> dict | None:
    rows = query(
        buyer_dsn(settings),
        f"SELECT * FROM {qualify(settings, 'briefs')} WHERE brief_id = %s",
        (brief_id,),
    )
    return rows[0] if rows else None


def get_buyer_identity(settings: Settings, buyer_id: str | None = None) -> dict | None:
    """The buyer's seat/agency/advertiser identity row (defaults to this app's buyer)."""
    rows = query(
        buyer_dsn(settings),
        f"SELECT * FROM {qualify(settings, 'buyer_identities')} WHERE buyer_id = %s",
        (buyer_id or settings.buyer_id,),
    )
    return rows[0] if rows else None
