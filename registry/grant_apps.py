#!/usr/bin/env python3
"""Register the registry app's service principal as a Postgres role on the registry
Lakebase project (Autoscaling tier) — same handshake as the seller's grant_apps.py.

The Provisioned `database` app-resource binding (which auto-creates the SP role) 404s
on an Autoscaling project, and the Autoscaling `postgres` binding needs a pre-registered
database the bundle can't order on — so the SP is registered as a role here, AFTER
`databricks bundle deploy` has created the app (and thus its SP). Combined with the
seed's `GRANT ... TO PUBLIC`, the SP can then connect AND read/write the kv_store.

Usage:  python registry/grant_apps.py <prefix> [profile]
"""

from __future__ import annotations

import sys

BRANCH = "production"


def main(prefix: str, profile: str | None) -> int:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service import postgres as pg

    w = WorkspaceClient(profile=profile) if profile and profile != "DEFAULT" else WorkspaceClient()
    branch = f"projects/{prefix}-registry/branches/{BRANCH}"
    existing = {r.status.postgres_role for r in w.postgres.list_roles(parent=branch)}

    app_name = f"{prefix}-registry-app"
    try:
        sp = w.apps.get(name=app_name).service_principal_client_id
    except Exception as e:
        print(f"  • {app_name}: app not found ({type(e).__name__}) — skip")
        return 0
    if sp in existing:
        print(f"  • {app_name}: SP {sp} already has a role — skip")
        return 0
    role = pg.Role(spec=pg.RoleRoleSpec(
        identity_type=pg.RoleIdentityType.SERVICE_PRINCIPAL,
        auth_method=pg.RoleAuthMethod.LAKEBASE_OAUTH_V1,
        postgres_role=sp,
    ))
    w.postgres.create_role(parent=branch, role=role, role_id="app-registry")
    print(f"  • {app_name}: granted (SP {sp} → role app-registry)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python registry/grant_apps.py <prefix> [profile]")
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
