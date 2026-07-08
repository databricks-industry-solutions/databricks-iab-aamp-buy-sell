"""Write a Universe to (1) relational tables, (2) the SDK kv_store, (3) SDK CSV inputs.

Two layers:
  • RELATIONAL source-of-truth — typed columns in schema `aamp` (TEXT/NUMERIC/INT/
    BOOLEAN, native arrays TEXT[]/INT[], JSONB only for genuinely nested fields).
    This is the human/governance view. Sell-side tables carry a `seller` column.
  • SDK kv_store — per-seller key/value store the seller-agent actually reads
    (`kv_<slug>.kv_store(key TEXT, value JSONB)`), materialized with the SDK's keys
    `product:{id}` / `package:{id}`. Each seller gets its own kv schema.

Inventory + audiences also export to per-seller CSVs (the SDK csv_adapter inputs).

DSNs per party (env): AAMP_SELLER_DSN, AAMP_BUYER_DSN, AAMP_REGISTRY_DSN.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from .generator import Universe, seller_slug

SCHEMA = "aamp"
KV_PREFIX = "kv_"
_PARTY_DSN_ENV = {"seller": "AAMP_SELLER_DSN", "buyer": "AAMP_BUYER_DSN", "registry": "AAMP_REGISTRY_DSN"}

# Relational column specs: (column, sql_type, kind)  kind: s=scalar a=array(list) j=jsonb
_REL: dict[str, list[tuple[str, str, str]]] = {
    "audiences": [
        ("seller", "TEXT", "s"), ("id", "TEXT", "s"), ("name", "TEXT", "s"),
        ("description", "TEXT", "s"), ("size", "BIGINT", "s"), ("segment_type", "TEXT", "s"),
        ("status", "TEXT", "s"), ("iab_audience_taxonomy_id", "TEXT", "s"),
    ],
    "inventory": [
        ("seller", "TEXT", "s"), ("id", "TEXT", "s"), ("name", "TEXT", "s"),
        ("parent_id", "TEXT", "s"), ("status", "TEXT", "s"), ("sizes", "TEXT", "s"),
        ("ad_formats", "TEXT[]", "a"), ("device_types", "INT[]", "a"),
        ("inventory_type", "TEXT", "s"), ("content_categories", "TEXT[]", "a"),
        ("floor_price_cpm", "NUMERIC", "s"), ("currency", "TEXT", "s"),
        ("geo_targets", "TEXT[]", "a"), ("description", "TEXT", "s"),
    ],
    "products": [
        ("seller", "TEXT", "s"), ("product_id", "TEXT", "s"), ("name", "TEXT", "s"),
        ("description", "TEXT", "s"), ("inventory_type", "TEXT", "s"),
        ("inventory_segment_ids", "TEXT[]", "a"), ("supported_deal_types", "TEXT[]", "a"),
        ("supported_pricing_models", "TEXT[]", "a"), ("base_cpm", "NUMERIC", "s"),
        ("floor_cpm", "NUMERIC", "s"), ("currency", "TEXT", "s"),
        ("minimum_impressions", "BIGINT", "s"), ("maximum_impressions", "BIGINT", "s"),
        ("audience_targeting", "JSONB", "j"), ("content_targeting", "JSONB", "j"),
        ("ad_product_targeting", "JSONB", "j"), ("audience_capabilities", "TEXT[]", "a"),
    ],
    "packages": [
        ("seller", "TEXT", "s"), ("package_id", "TEXT", "s"), ("name", "TEXT", "s"),
        ("description", "TEXT", "s"), ("layer", "TEXT", "s"), ("status", "TEXT", "s"),
        ("placements", "JSONB", "j"), ("cat", "TEXT[]", "a"), ("cattax", "INT", "s"),
        ("audience_capabilities", "JSONB", "j"), ("device_types", "INT[]", "a"),
        ("ad_formats", "TEXT[]", "a"), ("geo_targets", "TEXT[]", "a"),
        ("base_price", "NUMERIC", "s"), ("floor_price", "NUMERIC", "s"),
        ("rate_type", "TEXT", "s"), ("currency", "TEXT", "s"), ("tags", "TEXT[]", "a"),
        ("is_featured", "BOOLEAN", "s"),
    ],
    "pricing_rules": [
        ("seller", "TEXT", "s"), ("rule_id", "TEXT", "s"), ("rule_name", "TEXT", "s"),
        ("priority", "INT", "s"), ("access_tier", "TEXT", "s"), ("agency_ids", "TEXT[]", "a"),
        ("advertiser_ids", "TEXT[]", "a"), ("holding_company_ids", "TEXT[]", "a"),
        ("product_ids", "TEXT[]", "a"), ("inventory_types", "TEXT[]", "a"),
        ("base_price_override", "NUMERIC", "s"), ("discount_percentage", "NUMERIC", "s"),
        ("price_floor", "NUMERIC", "s"), ("price_ceiling", "NUMERIC", "s"),
        ("volume_discounts", "JSONB", "j"), ("negotiation_enabled", "BOOLEAN", "s"),
        ("max_negotiation_discount", "NUMERIC", "s"), ("valid_from", "TEXT", "s"),
        ("valid_to", "TEXT", "s"), ("is_active", "BOOLEAN", "s"),
    ],
    "buyer_identities": [
        ("buyer_id", "TEXT", "s"), ("name", "TEXT", "s"), ("seat_id", "TEXT", "s"),
        ("agency_id", "TEXT", "s"), ("agency_holding_company", "TEXT", "s"),
        ("advertiser_id", "TEXT", "s"), ("tier", "TEXT", "s"),
    ],
    "briefs": [
        ("brief_id", "TEXT", "s"), ("name", "TEXT", "s"), ("buyer_id", "TEXT", "s"),
        ("objectives", "TEXT[]", "a"), ("total_budget", "NUMERIC", "s"), ("currency", "TEXT", "s"),
        ("start_date", "TEXT", "s"), ("end_date", "TEXT", "s"), ("target_audience", "JSONB", "j"),
        ("kpis", "JSONB", "j"), ("channels", "JSONB", "j"), ("deal_preferences", "JSONB", "j"),
    ],
    "api_keys": [
        ("key_id", "TEXT", "s"), ("api_key", "TEXT", "s"), ("buyer_id", "TEXT", "s"),
        ("seat_id", "TEXT", "s"), ("agency_id", "TEXT", "s"), ("advertiser_id", "TEXT", "s"),
        ("name", "TEXT", "s"),
    ],
    "registry_agents": [
        ("agent_id", "TEXT", "s"), ("name", "TEXT", "s"), ("type", "TEXT", "s"),
        ("url", "TEXT", "s"), ("capabilities", "TEXT[]", "a"), ("trust_level", "TEXT", "s"),
        ("taxonomies", "TEXT[]", "a"), ("rate_card", "JSONB", "j"),
    ],
    "registry_trust": [
        ("agent_id", "TEXT", "s"), ("trust_status", "TEXT", "s"), ("tier_ceiling", "TEXT", "s"),
    ],
}
_PK = {"audiences": "id", "inventory": "id", "products": "product_id", "packages": "package_id",
       "pricing_rules": "rule_id", "buyer_identities": "buyer_id", "briefs": "brief_id",
       "api_keys": "key_id", "registry_agents": "agent_id", "registry_trust": "agent_id"}
_PARTY_TABLES = {
    "seller": ["inventory", "audiences", "products", "packages", "pricing_rules"],
    "buyer": ["buyer_identities", "briefs", "api_keys"],
    "registry": ["registry_agents", "registry_trust"],
}

# SDK csv_adapter headers (arrays are pipe-delimited).
_SDK_CSV = {
    "inventory": ["id", "name", "parent_id", "status", "sizes", "ad_formats", "device_types",
                  "inventory_type", "content_categories", "floor_price_cpm", "currency",
                  "geo_targets", "description"],
    "audiences": ["id", "name", "description", "size", "segment_type", "status",
                  "iab_audience_taxonomy_id"],
}


def _cell(v):
    return json.dumps(v) if isinstance(v, (list, dict)) else ("" if v is None else v)


def write_csvs(u: Universe, outdir: str | Path) -> Path:
    """Inspection CSVs: out/<party>/<table>.csv (arrays/objects JSON-encoded)."""
    base = Path(outdir)
    for party, tables in u.tables().items():
        for tname, rows in tables.items():
            d = base / party
            d.mkdir(parents=True, exist_ok=True)
            cols = [c for c, _, _ in _REL[tname]]
            with (d / f"{tname}.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(cols)
                for r in rows:
                    w.writerow([_cell(r.get(c)) for c in cols])
    return base


def write_sdk_csvs(u: Universe, outdir: str | Path) -> Path:
    """Per-seller SDK csv_adapter inputs: out/sdk/<slug>/{inventory,audiences}.csv (pipe arrays)."""
    base = Path(outdir) / "sdk"
    by_seller: dict[str, dict[str, list[dict]]] = {}
    for tname in ("inventory", "audiences"):
        for r in u.seller[tname]:
            by_seller.setdefault(r["seller"], {}).setdefault(tname, []).append(r)
    for sid, tabs in by_seller.items():
        d = base / seller_slug(sid)
        d.mkdir(parents=True, exist_ok=True)
        for tname, header in _SDK_CSV.items():
            with (d / f"{tname}.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(header)
                for r in tabs.get(tname, []):
                    w.writerow(["|".join(map(str, r[c])) if isinstance(r.get(c), list)
                                else ("" if r.get(c) is None else r.get(c)) for c in header])
    return base


def load_relational(u: Universe, schema: str = SCHEMA) -> dict[str, str]:
    """Full-replace each party DB's relational tables in `schema` (typed columns)."""
    try:
        import psycopg  # noqa
        from psycopg.types.json import Jsonb
    except ImportError:
        return {p: "skipped (psycopg not installed)" for p in u.tables()}
    import psycopg

    status: dict[str, str] = {}
    for party, tables in u.tables().items():
        dsn = os.environ.get(_PARTY_DSN_ENV[party])
        if not dsn:
            status[party] = f"skipped (no {_PARTY_DSN_ENV[party]})"
            continue
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            counts = []
            for tname, rows in tables.items():
                spec = _REL[tname]
                fq = f'"{schema}"."{tname}"'
                ddl = ", ".join(f'"{c}" {t}' + (" PRIMARY KEY" if c == _PK[tname] else "")
                                for c, t, _ in spec)
                cur.execute(f"DROP TABLE IF EXISTS {fq} CASCADE")
                cur.execute(f"CREATE TABLE {fq} ({ddl})")
                cols = [c for c, _, _ in spec]
                ph = ", ".join(["%s"] * len(cols))
                ins = f'INSERT INTO {fq} ({", ".join(chr(34)+c+chr(34) for c in cols)}) VALUES ({ph})'
                for r in rows:
                    vals = []
                    for c, _, kind in spec:
                        v = r.get(c)
                        vals.append(Jsonb(v) if (kind == "j" and v is not None) else v)
                    cur.execute(ins, vals)
                counts.append(f"{tname}={len(rows)}")
            # The seller app reads aamp.pricing_rules as its own service principal
            # (not the seeder). Grant the relational schema TO PUBLIC so any
            # auto-provisioned SP role can SELECT — mirrors the kv_store grants.
            cur.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO PUBLIC')
            cur.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO PUBLIC')
            status[party] = "; ".join(counts)
    return status


def materialize_kv(u: Universe, prefix: str = KV_PREFIX) -> dict[str, str]:
    """Materialize each seller's products+packages into its own kv_<slug>.kv_store (SDK format)."""
    dsn = os.environ.get(_PARTY_DSN_ENV["seller"])
    if not dsn:
        return {"seller": f"skipped (no {_PARTY_DSN_ENV['seller']})"}
    try:
        import psycopg  # noqa
        from psycopg.types.json import Jsonb
    except ImportError:
        return {"seller": "skipped (psycopg not installed)"}
    import psycopg

    by_seller: dict[str, dict[str, dict]] = {}
    for p in u.seller["products"]:
        obj = {k: v for k, v in p.items() if k != "seller"}
        by_seller.setdefault(p["seller"], {})[f"product:{p['product_id']}"] = obj
    for pk in u.seller["packages"]:
        obj = {k: v for k, v in pk.items() if k != "seller"}
        by_seller.setdefault(pk["seller"], {})[f"package:{pk['package_id']}"] = obj

    status: dict[str, str] = {}
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for sid, kv in by_seller.items():
            sch = f"{prefix}{seller_slug(sid)}"
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{sch}"')
            # Match the seller-agent SDK's PostgresBackend table EXACTLY so the SDK
            # backend reads/writes this store verbatim (expires_at DOUBLE PRECISION,
            # epoch seconds — NOT timestamptz; the SDK binds float epochs in its
            # TTL comparisons). Pre-create the SDK's index so the backend's
            # `CREATE INDEX IF NOT EXISTS` on connect is a no-op for non-owners.
            cur.execute(f'CREATE TABLE IF NOT EXISTS "{sch}".kv_store '
                        f"(key TEXT PRIMARY KEY, value JSONB NOT NULL, expires_at DOUBLE PRECISION)")
            cur.execute(f'CREATE INDEX IF NOT EXISTS idx_kv_expires '
                        f'ON "{sch}".kv_store (expires_at) WHERE expires_at IS NOT NULL')
            cur.execute(f'TRUNCATE "{sch}".kv_store')
            for key, obj in kv.items():
                cur.execute(f'INSERT INTO "{sch}".kv_store (key, value) VALUES (%s, %s) '
                            f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                            (key, Jsonb(obj)))
            # The seller app connects as its own principal (not the seeder); grant
            # the catalog kv store so the SDK backend can serve + persist runtime state.
            cur.execute(f'GRANT USAGE ON SCHEMA "{sch}" TO PUBLIC')
            cur.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{sch}".kv_store TO PUBLIC')
            status[sid] = f'{len(kv)} keys → "{sch}".kv_store'
    return status


def provision_registry_kv(schema: str = "kv_registry") -> dict[str, str]:
    """Create the registry app's kv_store (empty) in the agent_registry DB.

    The registry's agents are registered DYNAMICALLY (the live seller apps, via
    `make register-sellers`), so there's no static data to load here — we only
    provision the SDK-exact table the stateless registry app reads/writes (its
    LakebaseKVStore.connect() runs no DDL) and grant it to the app's principal.
    Table shape matches the SDK PostgresBackend EXACTLY (expires_at DOUBLE PRECISION).
    """
    dsn = os.environ.get(_PARTY_DSN_ENV["registry"])
    if not dsn:
        return {"registry": f"skipped (no {_PARTY_DSN_ENV['registry']})"}
    try:
        import psycopg  # noqa
    except ImportError:
        return {"registry": "skipped (psycopg not installed)"}
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        cur.execute(f'CREATE TABLE IF NOT EXISTS "{schema}".kv_store '
                    f"(key TEXT PRIMARY KEY, value JSONB NOT NULL, expires_at DOUBLE PRECISION)")
        cur.execute(f'CREATE INDEX IF NOT EXISTS idx_kv_expires '
                    f'ON "{schema}".kv_store (expires_at) WHERE expires_at IS NOT NULL')
        # The registry app connects as its own service principal (not the seeder).
        cur.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO PUBLIC')
        cur.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{schema}".kv_store TO PUBLIC')
    return {"registry": f'kv_store ready → "{schema}".kv_store'}


def drop_schema(schema: str = SCHEMA) -> dict[str, str]:
    """Full wipe: DROP `aamp` (all DBs) + all kv_* schemas (seller DB)."""
    try:
        import psycopg  # noqa
    except ImportError:
        return {p: "skipped (psycopg not installed)" for p in _PARTY_DSN_ENV}
    import psycopg
    status: dict[str, str] = {}
    for party, env in _PARTY_DSN_ENV.items():
        dsn = os.environ.get(env)
        if not dsn:
            status[party] = f"skipped (no {env})"
            continue
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            dropped = [schema]
            if party == "seller":
                cur.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (f"{KV_PREFIX}%",))
                for (sch,) in cur.fetchall():
                    cur.execute(f'DROP SCHEMA IF EXISTS "{sch}" CASCADE')
                    dropped.append(sch)
            status[party] = f"dropped {', '.join(dropped)}"
    return status
