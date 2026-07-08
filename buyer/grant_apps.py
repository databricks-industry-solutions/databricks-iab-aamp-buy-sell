#!/usr/bin/env python3
"""Grant the buyer app's service principal the data + model access its crews need:

  1. A Postgres role on the buyer AND registry Lakebase projects (same handshake as
     seller/registry grant_apps.py). The buyer always reads its own briefs/identities
     (buyer project); it also reads the registry project directly in the Lakebase-fallback
     discovery path (when AAMP_REGISTRY_URL is unset), so it's granted there too.
  2. CAN_QUERY on the Model Serving endpoint(s) the L1/L2/L3 crews call, so CrewAI can
     reach Databricks Model Serving with the app's SP token.

App-to-app CAN_USE grants (buyer SP → registry + seller apps, for MCP through the OAuth
proxy) live in the dedicated `grant_app_access.py` (`make grant-app-access`).

Run AFTER `databricks bundle deploy` has created the app (and thus its SP). Combined with
the seed's `GRANT ... TO PUBLIC`, the SP can then connect and read.

The serving endpoints default to the buyer's configured models (databricks-claude-sonnet-4-5);
override with AAMP_LLM_ENDPOINTS="ep1,ep2" (e.g. add databricks-claude-opus-4-8 for a
stronger L1 manager). Note: Databricks-hosted pay-per-token foundation-model endpoints have
no per-endpoint ACL — access is governed by the workspace foundation-model entitlement, which
the app SP inherits — so those are skipped; the CAN_QUERY grant applies to custom /
provisioned-throughput endpoints (which expose an id). Both steps are idempotent and
best-effort — an already-granted role/permission is skipped, and missing endpoints are
reported, not fatal.

Usage:  python buyer/grant_apps.py <prefix> [profile]
"""

from __future__ import annotations

import os
import sys

BRANCH = "production"
PROJECTS = ["buyer", "registry"]
DEFAULT_LLM_ENDPOINTS = ["databricks-claude-sonnet-4-5"]


def _llm_endpoints() -> list[str]:
    """Serving endpoints to grant CAN_QUERY on — AAMP_LLM_ENDPOINTS override, else default."""
    raw = os.environ.get("AAMP_LLM_ENDPOINTS", "")
    eps = [e.strip() for e in raw.split(",") if e.strip()] if raw else list(DEFAULT_LLM_ENDPOINTS)
    seen: dict[str, None] = {}
    for e in eps:  # dedupe, preserve order
        seen.setdefault(e, None)
    return list(seen)


def _grant_serving(w, sp: str) -> None:
    """Grant the app SP CAN_QUERY on each Model Serving endpoint the crews use.

    Custom and provisioned-throughput endpoints expose a per-endpoint id and take an ACL,
    so we grant CAN_QUERY there. Databricks-hosted pay-per-token foundation-model endpoints
    (e.g. databricks-claude-sonnet-4-5) have NO per-endpoint id/ACL — access is governed by
    the workspace foundation-model entitlement, which the app SP inherits — so there is
    nothing to grant and we skip them with a note."""
    from databricks.sdk.service.serving import (
        ServingEndpointAccessControlRequest as Acl,
        ServingEndpointPermissionLevel as Level,
    )

    for ep in _llm_endpoints():
        try:
            info = w.serving_endpoints.get(ep)
        except Exception as e:  # endpoint not in this workspace / no access
            print(f"  • serving '{ep}': not found ({type(e).__name__}) — skip")
            continue
        endpoint_id = getattr(info, "id", None)
        if not endpoint_id:
            etype = getattr(info, "endpoint_type", None) or "foundation-model"
            print(f"  • serving '{ep}': system {etype} endpoint has no per-endpoint ACL — "
                  f"access is workspace-governed, SP inherits it; nothing to grant")
            continue
        try:
            # update_permissions MERGES (adds) the ACL entry — it does not clobber existing
            # grants — so re-running is safe/idempotent.
            w.serving_endpoints.update_permissions(
                serving_endpoint_id=endpoint_id,
                access_control_list=[Acl(service_principal_name=sp, permission_level=Level.CAN_QUERY)],
            )
            print(f"  • serving '{ep}': CAN_QUERY granted to SP {sp}")
        except Exception as e:
            print(f"  • serving '{ep}': grant failed ({type(e).__name__}: {e})")


def main(prefix: str, profile: str | None) -> int:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service import postgres as pg

    w = WorkspaceClient(profile=profile) if profile and profile != "DEFAULT" else WorkspaceClient()

    app_name = f"{prefix}-buyer-app"
    try:
        sp = w.apps.get(name=app_name).service_principal_client_id
    except Exception as e:
        print(f"  • {app_name}: app not found ({type(e).__name__}) — skip")
        return 0

    for proj in PROJECTS:
        branch = f"projects/{prefix}-{proj}/branches/{BRANCH}"
        existing = {r.status.postgres_role for r in w.postgres.list_roles(parent=branch)}
        if sp in existing:
            print(f"  • {app_name} → {proj}: SP {sp} already has a role — skip")
            continue
        role = pg.Role(spec=pg.RoleRoleSpec(
            identity_type=pg.RoleIdentityType.SERVICE_PRINCIPAL,
            auth_method=pg.RoleAuthMethod.LAKEBASE_OAUTH_V1,
            postgres_role=sp,
        ))
        w.postgres.create_role(parent=branch, role=role, role_id="app-buyer")
        print(f"  • {app_name} → {proj}: granted (SP {sp} → role app-buyer)")

    _grant_serving(w, sp)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python buyer/grant_apps.py <prefix> [profile]")
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
