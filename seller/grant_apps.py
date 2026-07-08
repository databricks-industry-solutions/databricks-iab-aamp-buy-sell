#!/usr/bin/env python3
"""Register each seller app's service principal as a Postgres role on the seller
Lakebase project (Autoscaling tier) — the seed→app permission handshake.

WHY THIS EXISTS: the Provisioned-tier `database` app-resource binding auto-creates
the SP's Postgres role, but it 404s on an Autoscaling project. The Autoscaling
`postgres` binding needs the target database pre-registered with a stable id before
the bundle deploys the app — and there's no `postgres_database` bundle resource to
order that on. So instead each app's SP is registered as a role here (idempotent),
AFTER `databricks bundle deploy` has created the apps (and thus their SPs). Combined
with the seed's `GRANT ... TO PUBLIC`, the SP role can then connect AND read.

Usage:  python seller/grant_apps.py <prefix> [profile]
"""

from __future__ import annotations

import sys

SELLERS = ["summit", "beacon"]
BRANCH = "production"


def main(prefix: str, profile: str | None) -> int:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service import postgres as pg

    w = WorkspaceClient(profile=profile) if profile and profile != "DEFAULT" else WorkspaceClient()
    branch = f"projects/{prefix}-seller/branches/{BRANCH}"
    existing = {r.status.postgres_role for r in w.postgres.list_roles(parent=branch)}

    for slug in SELLERS:
        app_name = f"{prefix}-seller-{slug}"
        try:
            sp = w.apps.get(name=app_name).service_principal_client_id
        except Exception as e:
            print(f"  • {app_name}: app not found ({type(e).__name__}) — skip")
            continue
        if sp in existing:
            print(f"  • {app_name}: SP {sp} already has a role — skip")
            continue
        role = pg.Role(spec=pg.RoleRoleSpec(
            identity_type=pg.RoleIdentityType.SERVICE_PRINCIPAL,
            auth_method=pg.RoleAuthMethod.LAKEBASE_OAUTH_V1,
            postgres_role=sp,
        ))
        w.postgres.create_role(parent=branch, role=role, role_id=f"app-seller-{slug}")
        print(f"  • {app_name}: granted (SP {sp} → role app-seller-{slug})")
    print("✓ app service principals can now connect; the seed's GRANT ... TO PUBLIC covers data access.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python seller/grant_apps.py <prefix> [profile]")
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
