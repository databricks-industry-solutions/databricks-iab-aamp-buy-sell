"""Lakebase connection seams (Autoscaling tier) with SHARED, idle-draining pools.

Statelessness is about DATA, not transport. Every request still queries Lakebase
FRESH (no cached pricing/catalog — a price edited in the DB shows on the next call),
but the CONNECTIONS are pooled and reused so we don't reconnect + mint an OAuth
credential on every call. Both pools use min_size=0 and a 30-min idle timeout: when
traffic stops they drain to zero connections so the endpoint can still scale to
zero; a near-stale-token pool is recycled before the ~1-hour OAuth expiry.

  shared_kv_store(settings)     -> a long-lived LakebaseKVStore (asyncpg pool) for
                                   the SDK PostgresBackend (products/packages/quotes…).
  fetch_pricing_rules(settings) -> relational read of aamp.pricing_rules via a shared
                                   psycopg pool (fresh query, reused connection).
"""

from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import Settings

IDLE_SECONDS = 1800.0      # close a connection unused for 30 min → endpoint can suspend
RECYCLE_SECONDS = 2700.0   # rebuild the pool before the ~1h OAuth token expires


def _client(settings: Settings):
    from databricks.sdk import WorkspaceClient

    return (
        WorkspaceClient(profile=settings.databricks_profile)
        if settings.databricks_profile
        else WorkspaceClient()
    )


def _token(settings: Settings) -> str:
    """Mint a short-lived Lakebase OAuth credential for the endpoint (Autoscaling tier)."""
    w = _client(settings)
    return w.postgres.generate_database_credential(
        endpoint=os.environ.get("ENDPOINT_NAME", settings.seller_endpoint_path)
    ).token


def _conn_params(settings: Settings) -> dict:
    """Resolve host/user/port/db (Autoscaling postgres API). Runtime-injected PG* vars
    win when present; otherwise resolve the host from the endpoint + caller identity.
    Called once per pool build (not per request), so the control-plane calls are rare."""
    w = _client(settings)
    host = os.environ.get("PGHOST")
    if not host:
        endpoint = os.environ.get("ENDPOINT_NAME", settings.seller_endpoint_path)
        host = w.postgres.get_endpoint(name=endpoint).status.hosts.host
    user = os.environ.get("PGUSER") or w.current_user.me().user_name
    return {
        "host": host,
        "user": user,
        "port": os.environ.get("PGPORT", "5432"),
        "dbname": os.environ.get("PGDATABASE", settings.seller_database),
    }


def _base_url(settings: Settings) -> str:
    if settings.seller_dsn:
        return settings.seller_dsn
    p = _conn_params(settings)
    return (
        f"postgresql://{quote(p['user'], safe='')}:{quote(_token(settings), safe='')}"
        f"@{p['host']}:{p['port']}/{p['dbname']}?sslmode=require"
    )


def seller_kv_dsn(settings: Settings) -> str:
    """asyncpg URL pointed at the seller's kv schema via search_path."""
    base = _base_url(settings)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}options=-c%20search_path%3D{settings.kv_schema}"


# ---------------------------------------------------------------------------
# Shared asyncpg kv store — the SDK PostgresBackend, reused across requests.
# ---------------------------------------------------------------------------
_kv = None
_kv_born = 0.0
_kv_loop = None       # event loop the pool is bound to (asyncpg pools are loop-bound)
_kv_lock = None


async def shared_kv_store(settings: Settings):
    """Return a long-lived, connected LakebaseKVStore. Reused across requests;
    rebuilt only when the OAuth token nears expiry (or the event loop changes — e.g.
    per-test loops). The pool (min_size=0, 30-min idle) drains to zero when idle so
    the endpoint can still scale to zero."""
    from .kvstore import LakebaseKVStore  # local import: avoid import cycle

    global _kv, _kv_born, _kv_loop, _kv_lock
    loop = asyncio.get_running_loop()
    if _kv_lock is None or _kv_loop is not loop:
        _kv_lock = asyncio.Lock()  # a lock bound to the current loop
    async with _kv_lock:
        stale = _kv is not None and (time.monotonic() - _kv_born) > RECYCLE_SECONDS
        if _kv is not None and (_kv_loop is not loop or stale):
            if _kv_loop is loop:
                asyncio.create_task(_drain(_kv))  # same loop → drain old pool
            _kv = None
        if _kv is None:
            kv = LakebaseKVStore(seller_kv_dsn(settings))
            await kv.connect()
            _kv, _kv_born, _kv_loop = kv, time.monotonic(), loop
        return _kv


async def _drain(store) -> None:
    try:
        await store.disconnect()  # waits for in-flight, then closes
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Shared psycopg pool — relational pricing_rules read (fresh query, reused conn).
# ---------------------------------------------------------------------------
_pricing_pool: ConnectionPool | None = None
_pricing_born = 0.0


class _OAuthConn(psycopg.Connection):
    """Mints a fresh Lakebase OAuth token for each physical connection the pool opens."""

    settings: Settings | None = None

    @classmethod
    def connect(cls, conninfo: str = "", **kwargs):
        kwargs["password"] = _token(cls.settings)
        return super().connect(conninfo, **kwargs)


def _pricing_pool_for(settings: Settings) -> ConnectionPool:
    global _pricing_pool, _pricing_born
    if _pricing_pool is not None and (time.monotonic() - _pricing_born) > RECYCLE_SECONDS:
        try:
            _pricing_pool.close()
        except Exception:
            pass
        _pricing_pool = None
    if _pricing_pool is None:
        p = _conn_params(settings)
        conninfo = f"host={p['host']} port={p['port']} dbname={p['dbname']} user={p['user']} sslmode=require"
        _OAuthConn.settings = settings
        _pricing_pool = ConnectionPool(
            conninfo=conninfo, connection_class=_OAuthConn,
            min_size=0, max_size=4, max_idle=IDLE_SECONDS, max_lifetime=RECYCLE_SECONDS, open=True,
        )
        _pricing_born = time.monotonic()
    return _pricing_pool


def _fetch_relational(settings: Settings, table: str) -> list[dict]:
    """FRESH relational read of aamp.<table> for this seller, over a reused pooled
    connection. (BYO DSN path stays one-shot; perf-uncritical.)"""
    sql = f'SELECT * FROM "{settings.schema_name}"."{table}" WHERE seller = %s'
    if settings.seller_dsn:
        with psycopg.connect(settings.seller_dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(sql, (settings.org_id,))
            return [dict(r) for r in cur.fetchall()]
    pool = _pricing_pool_for(settings)
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (settings.org_id,))
        return [dict(r) for r in cur.fetchall()]


def fetch_pricing_rules(settings: Settings) -> list[dict]:
    """Relational read of aamp.pricing_rules for this seller (FRESH per call, pooled)."""
    return _fetch_relational(settings, "pricing_rules")


def fetch_audiences(settings: Settings) -> list[dict]:
    """Relational read of aamp.audiences for this seller — names/sizes/IAB ids that the
    kv package objects only carry as segment-id codes. FRESH per call, pooled."""
    return _fetch_relational(settings, "audiences")
