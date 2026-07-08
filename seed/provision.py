"""Provision / connect / teardown the 3 Lakebase projects (Autoscaling tier).

Uses the Databricks SDK `w.postgres` (Autoscaling: projects → branches → endpoints).
No secrets stored — the OAuth credential is minted at runtime from your Databricks
auth. The bundle creates the projects; the seed connects to them (create=False).

  instance_dsns(prefix, profile, create=True)  -> {party: DSN}   (create project if missing)
  instance_dsns(prefix, profile, create=False) -> {party: DSN}   (existing only)
  delete_instances(prefix, profile)            -> {party: status} (full teardown)
"""

from __future__ import annotations

from urllib.parse import quote

from .config import PARTY_DB

BRANCH = "production"
ENDPOINT = "primary"
PARTIES = ("buyer", "seller", "registry")


def _client(profile: str | None):
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient(profile=profile) if profile and profile != "DEFAULT" else WorkspaceClient()


def _project(prefix: str, party: str) -> str:
    return f"projects/{prefix}-{party}"


def _endpoint_path(prefix: str, party: str) -> str:
    return f"{_project(prefix, party)}/branches/{BRANCH}/endpoints/{ENDPOINT}"


def plan(prefix: str) -> list[str]:
    return [f"{prefix}-{p}  (autoscaling project, database: {PARTY_DB[p]})" for p in PARTIES]


def _dsn(user: str, token: str, host: str, dbname: str) -> str:
    # user is an email (contains '@'); token may contain URL-reserved chars → encode both.
    return f"postgresql://{quote(user, safe='')}:{quote(token, safe='')}@{host}:5432/{dbname}?sslmode=require"


def _ensure_database(admin_dsn: str, dbname: str) -> None:
    import psycopg
    with psycopg.connect(admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{dbname}"')


def instance_dsns(prefix: str, profile: str | None = None, create: bool = True) -> dict[str, str]:
    """Return {party: DSN} for each Autoscaling project's primary endpoint.

    Always ensures the party database (e.g. ad_seller) exists — the project's
    default db is `databricks_postgres`. create=True will create a missing project
    (standalone `make up`); under the DAB the projects already exist so create=False.
    """
    from databricks.sdk.service.postgres import Project

    w = _client(profile)
    user = w.current_user.me().user_name
    dsns: dict[str, str] = {}
    for party in PARTIES:
        proj, ep = _project(prefix, party), _endpoint_path(prefix, party)
        try:
            w.postgres.get_project(name=proj)
        except Exception:
            if not create:
                raise RuntimeError(f"project '{proj}' not found (deploy the bundle first)")
            print(f"  • {proj}: creating — a few minutes…")
            w.postgres.create_project(project=Project(), project_id=f"{prefix}-{party}").result()
        host = w.postgres.get_endpoint(name=ep).status.hosts.host
        token = w.postgres.generate_database_credential(endpoint=ep).token
        dbname = PARTY_DB[party]
        _ensure_database(_dsn(user, token, host, "databricks_postgres"), dbname)
        print(f"  • {proj}: database '{dbname}' ready")
        dsns[party] = _dsn(user, token, host, dbname)
    return dsns


# Back-compat alias
def provision(prefix: str, profile: str | None = None) -> dict[str, str]:
    return instance_dsns(prefix, profile, create=True)


def delete_instances(prefix: str, profile: str | None = None) -> dict[str, str]:
    """Full teardown: delete the 3 Lakebase projects (purges data)."""
    w = _client(profile)
    status: dict[str, str] = {}
    for party in PARTIES:
        proj = _project(prefix, party)
        try:
            w.postgres.delete_project(name=proj)
            status[party] = f"{proj}: deleted"
        except Exception as e:
            status[party] = f"{proj}: not deleted ({type(e).__name__})"
    return status
