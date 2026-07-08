#!/usr/bin/env python3
"""Grant cross-app invoke access so the BUYER agent can call the SELLER apps' MCP
through the Databricks Apps OAuth proxy.

Why this isn't in the bundle: each app's service principal is minted by the platform
when the bundle CREATES the app, so the buyer's SP id isn't known at plan time and
can't be referenced in the seller apps' `permissions` block during the same deploy
(chicken-and-egg). So — like `*/grant_apps.py` for Lakebase roles — this runs
post-deploy and is idempotent.

Grants: the buyer app's SP → CAN_USE on the registry app AND each seller app. The buyer
calls the registry (seller discovery + trust verify) and each seller's MCP over HTTP when
AAMP_REGISTRY_URL is set; the Lakebase roles those same calls fall back to are handled by
buyer/grant_apps.py. (The seller→registry trust loop is deferred.)

Usage:  python grant_app_access.py <prefix> [profile]
"""

from __future__ import annotations

import sys

SELLERS = ["summit", "beacon"]


def main(prefix: str, profile: str | None) -> int:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.apps import AppAccessControlRequest, AppPermissionLevel

    w = WorkspaceClient(profile=profile) if profile and profile != "DEFAULT" else WorkspaceClient()
    buyer_app = f"{prefix}-buyer-app"
    try:
        buyer_sp = w.apps.get(name=buyer_app).service_principal_client_id
    except Exception as e:  # noqa: BLE001
        print(f"  • {buyer_app}: app not found ({type(e).__name__}) — skip")
        return 0

    targets = [f"{prefix}-registry-app"] + [f"{prefix}-seller-{slug}" for slug in SELLERS]
    for target_app in targets:
        try:
            w.apps.update_permissions(
                target_app,
                access_control_list=[AppAccessControlRequest(
                    service_principal_name=buyer_sp,
                    permission_level=AppPermissionLevel.CAN_USE,
                )],
            )
            print(f"  • {target_app}: granted CAN_USE → buyer SP {buyer_sp}")
        except Exception as e:  # noqa: BLE001
            print(f"  • {target_app}: grant FAILED ({type(e).__name__}: {e})")
    print("✓ buyer app SP can now invoke the registry + seller apps through the OAuth proxy.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python grant_app_access.py <prefix> [profile]")
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
