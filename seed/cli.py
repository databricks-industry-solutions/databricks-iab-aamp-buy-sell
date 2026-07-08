"""CLI for the AAMP data layer (2 sellers: CTV + Linear; no themes).

  python -m seed preview              # generate + validate + inspection CSVs (no DB)
  python -m seed validate             # generate + validate only
  python -m seed seed                 # relational tables + kv_store + SDK CSVs (uses config DSNs)
  python -m seed kv                   # (re)materialize the SDK kv_store only
  python -m seed up [--plan]          # provision Lakebase (from config) then seed
  python -m seed reset                # DROP aamp + kv_* schemas (keep instances)
  python -m seed down --yes           # DELETE the 3 Lakebase instances
"""

from __future__ import annotations

import argparse
import os
import sys

from .generator import generate, load_buyers, load_sellers
from .load import (drop_schema, load_relational, materialize_kv, provision_registry_kv,
                   write_csvs, write_sdk_csvs)
from .validate import validate

_DSN_ENV = {"seller": "AAMP_SELLER_DSN", "buyer": "AAMP_BUYER_DSN", "registry": "AAMP_REGISTRY_DSN"}


def _ensure_dsns_from_config(cfg, prefix: str | None = None) -> None:
    if any(os.environ.get(e) for e in _DSN_ENV.values()):
        return
    from .provision import PARTIES, instance_dsns
    # `prefix` (from --prefix / the DAB var) overrides config.yaml so the seed job
    # always targets the SAME instances the bundle just deployed.
    instance_prefix = prefix or cfg["instance_prefix"]
    if cfg.get("provision_lakebase", True):
        dsns = instance_dsns(instance_prefix, profile=cfg["workspace_profile"], create=False)
    else:
        dsns = {p: cfg.get("dsn", {}).get(p, "") for p in PARTIES}
    for party, dsn in dsns.items():
        if dsn:
            os.environ[_DSN_ENV[party]] = dsn


def _universe():
    return generate(load_sellers(), load_buyers())


def _validate_or_die(u) -> bool:
    errors = validate(u)
    if errors:
        print(f"✗ {len(errors)} validation error(s):")
        for e in errors:
            print("    -", e)
        return False
    return True


def cmd_preview(args) -> int:
    u = _universe()
    if not _validate_or_die(u):
        return 1
    base = write_csvs(u, args.out)
    write_sdk_csvs(u, args.out)
    s = sum(len(t) for t in u.seller.values())
    print(f"✓ generated + validated → {base}  (seller rows: {s}; SDK CSVs in {args.out}/sdk)")
    return 0


def cmd_validate(_args) -> int:
    u = _universe()
    if not _validate_or_die(u):
        return 1
    print("✓ coherent (single-channel sellers, no cross-seller products, 3-way trust/tier).")
    return 0


def cmd_seed(args) -> int:
    u = _universe()
    if not _validate_or_die(u):
        return 1
    try:
        from .config import load_config
        _ensure_dsns_from_config(load_config(args.config), prefix=getattr(args, "prefix", None))
    except Exception as e:
        print(f"(note: no Lakebase connection from config — CSV only: {type(e).__name__})")
    write_csvs(u, args.out)
    write_sdk_csvs(u, args.out)
    rel = load_relational(u)
    kv = materialize_kv(u)
    reg = provision_registry_kv()
    print("✓ relational: " + "; ".join(f"{k}=[{v}]" for k, v in rel.items()))
    print("✓ kv_store:   " + "; ".join(f"{k}: {v}" for k, v in kv.items()))
    print("✓ registry:   " + "; ".join(f"{k}: {v}" for k, v in reg.items()))
    return 0


def cmd_kv(args) -> int:
    """Re-materialize ONLY the SDK kv_store (relational already loaded)."""
    from .config import load_config
    _ensure_dsns_from_config(load_config(args.config), prefix=getattr(args, "prefix", None))
    kv = materialize_kv(_universe())
    reg = provision_registry_kv()
    print("✓ kv_store: " + "; ".join(f"{k}: {v}" for k, v in kv.items()))
    print("✓ registry: " + "; ".join(f"{k}: {v}" for k, v in reg.items()))
    return 0


def cmd_reset(args) -> int:
    from .config import load_config
    _ensure_dsns_from_config(load_config(args.config))
    status = drop_schema()
    print("reset (dropped aamp + kv_* schemas): " + "; ".join(f"{k}={v}" for k, v in status.items()))
    print("→ re-seed with:  python -m seed seed")
    return 0


def cmd_up(args) -> int:
    from .config import load_config
    from .provision import PARTIES, plan as provision_plan, provision

    cfg = load_config(args.config)
    prefix = cfg["instance_prefix"]
    print(f"Workspace profile: {cfg['workspace_profile']}  |  prefix: {prefix}  |  sellers: CTV + Linear")
    if cfg.get("provision_lakebase", True):
        print("Will provision (idempotent):")
        for line in provision_plan(prefix):
            print("   -", line)
    if args.plan:
        print("\n(--plan) no changes made.")
        return 0
    if cfg.get("provision_lakebase", True):
        print("\nProvisioning Lakebase…")
        dsns = provision(prefix, profile=cfg["workspace_profile"])
    else:
        dsns = {p: cfg.get("dsn", {}).get(p, "") for p in PARTIES}
    for party, dsn in dsns.items():
        if dsn:
            os.environ[_DSN_ENV[party]] = dsn
    print("\nSeeding…")
    return cmd_seed(args)


def cmd_down(args) -> int:
    from .config import load_config
    from .provision import PARTIES, delete_instances
    cfg = load_config(args.config)
    prefix = cfg["instance_prefix"]
    targets = ", ".join(f"{prefix}-{p}" for p in PARTIES)
    print(f"⚠️  This DELETES Lakebase instances: {targets} (purges ALL data, not reversible).")
    if not args.yes:
        print("Re-run with --yes to confirm.")
        return 1
    status = delete_instances(prefix, profile=cfg["workspace_profile"])
    print("teardown: " + "; ".join(f"{k}={v}" for k, v in status.items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="seed", description="AAMP demo data layer (CTV + Linear sellers)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("preview", help="generate + validate + inspection CSVs (no DB)")
    pv.add_argument("--out", default="out"); pv.set_defaults(func=cmd_preview)

    sub.add_parser("validate", help="generate + validate only").set_defaults(func=cmd_validate)

    sd = sub.add_parser("seed", help="relational tables + kv_store + SDK CSVs")
    sd.add_argument("--out", default="out"); sd.add_argument("--config", default="config.yaml")
    sd.add_argument("--prefix", default=None, help="Lakebase instance prefix (overrides config; set by the DAB var)")
    sd.set_defaults(func=cmd_seed)

    kv = sub.add_parser("kv", help="(re)materialize the SDK kv_store only")
    kv.add_argument("--config", default="config.yaml"); kv.set_defaults(func=cmd_kv)

    up = sub.add_parser("up", help="provision Lakebase (from config) then seed")
    up.add_argument("--config", default="config.yaml"); up.add_argument("--plan", action="store_true")
    up.add_argument("--out", default="out"); up.set_defaults(func=cmd_up)

    rs = sub.add_parser("reset", help="DROP aamp + kv_* schemas in all DBs (keep instances)")
    rs.add_argument("--config", default="config.yaml"); rs.set_defaults(func=cmd_reset)

    dn = sub.add_parser("down", help="DELETE the 3 Lakebase instances")
    dn.add_argument("--config", default="config.yaml"); dn.add_argument("--yes", action="store_true")
    dn.set_defaults(func=cmd_down)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
