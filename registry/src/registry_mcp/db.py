"""Lakebase connection seam (Autoscaling tier) with a SHARED, idle-draining pool.

Statelessness is about DATA, not transport. Every request still queries Lakebase
fresh (no cached directory/trust), but the kv connection pool is reused across
requests instead of reconnecting + minting a credential each call. The pool uses
min_size=0 + a 30-min idle timeout so it drains to zero when idle (endpoint can
scale to zero), and is recycled before the ~1-hour OAuth token expiry.

  shared_kv_store(settings) -> a long-lived LakebaseKVStore (asyncpg pool) for the
                               SDK PostgresBackend (agents + url index).
"""

from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import quote

from .config import Settings

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
        endpoint=os.environ.get("ENDPOINT_NAME", settings.registry_endpoint_path)
    ).token


def _conn_params(settings: Settings) -> dict:
    """Resolve host/user/port/db (Autoscaling postgres API); runtime-injected PG* vars
    win. Called once per pool build (not per request)."""
    w = _client(settings)
    host = os.environ.get("PGHOST")
    if not host:
        endpoint = os.environ.get("ENDPOINT_NAME", settings.registry_endpoint_path)
        host = w.postgres.get_endpoint(name=endpoint).status.hosts.host
    user = os.environ.get("PGUSER") or w.current_user.me().user_name
    return {
        "host": host,
        "user": user,
        "port": os.environ.get("PGPORT", "5432"),
        "dbname": os.environ.get("PGDATABASE", settings.registry_database),
    }


def _base_url(settings: Settings) -> str:
    if settings.registry_dsn:
        return settings.registry_dsn
    p = _conn_params(settings)
    return (
        f"postgresql://{quote(p['user'], safe='')}:{quote(_token(settings), safe='')}"
        f"@{p['host']}:{p['port']}/{p['dbname']}?sslmode=require"
    )


def registry_kv_dsn(settings: Settings) -> str:
    """asyncpg URL pointed at the registry's kv schema via search_path."""
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
    """Return a long-lived, connected LakebaseKVStore. Reused across requests; rebuilt
    only when the OAuth token nears expiry (or the event loop changes). Pool (min_size=0,
    30-min idle) drains to zero when idle so the endpoint can still scale to zero."""
    from .kvstore import LakebaseKVStore  # local import: avoid import cycle

    global _kv, _kv_born, _kv_loop, _kv_lock
    loop = asyncio.get_running_loop()
    if _kv_lock is None or _kv_loop is not loop:
        _kv_lock = asyncio.Lock()
    async with _kv_lock:
        stale = _kv is not None and (time.monotonic() - _kv_born) > RECYCLE_SECONDS
        if _kv is not None and (_kv_loop is not loop or stale):
            if _kv_loop is loop:
                asyncio.create_task(_drain(_kv))
            _kv = None
        if _kv is None:
            kv = LakebaseKVStore(registry_kv_dsn(settings))
            await kv.connect()
            _kv, _kv_born, _kv_loop = kv, time.monotonic(), loop
        return _kv


async def _drain(store) -> None:
    try:
        await store.disconnect()
    except Exception:
        pass
