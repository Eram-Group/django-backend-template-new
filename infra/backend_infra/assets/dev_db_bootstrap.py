"""Create (or re-key) one app's database on the shared dev RDS instance.

Runs INSIDE the VPC as a one-off ECS task (see constructs/dev_db_bootstrap.py)
so the master password never leaves AWS and nobody has to reach the private
instance from a laptop. Idempotent: re-running rotates the app password.

Env: MASTER_HOST, MASTER_USER, MASTER_PASSWORD (secret), APP_DB, TARGET_SECRET
Writes DATABASE_URL (plus SECRET_KEY / DJANGO_SUPERUSER_PASSWORD when empty)
into TARGET_SECRET's JSON.
"""

import json
import os
import secrets

import boto3
import psycopg
from psycopg import sql

host = os.environ["MASTER_HOST"]
app_db = os.environ["APP_DB"]
target = os.environ["TARGET_SECRET"]
password = secrets.token_urlsafe(32)  # URL-safe alphabet: no escaping needed

# Application secrets are never generated here - a human sets them in Secrets
# Manager. Refuse to leave the environment half-configured.
sm = boto3.client("secretsmanager")
current = json.loads(sm.get_secret_value(SecretId=target)["SecretString"])
missing = [k for k in ("SECRET_KEY", "DJANGO_SUPERUSER_PASSWORD") if not current.get(k)]
if missing:
    raise SystemExit(f"set {', '.join(missing)} in the {target} secret first")

with psycopg.connect(
    host=host,
    user=os.environ["MASTER_USER"],
    password=os.environ["MASTER_PASSWORD"],
    dbname="postgres",
    autocommit=True,
    sslmode="require",
) as conn:
    role = sql.Identifier(app_db)
    if (
        conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_db,)).fetchone()
        is None
    ):
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(role))
    conn.execute(
        sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(role, sql.Literal(password))
    )
    if (
        conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (app_db,)
        ).fetchone()
        is None
    ):
        # RDS master is not a superuser: it must be a member of a role to
        # hand it database ownership.
        conn.execute(sql.SQL("GRANT {} TO CURRENT_USER").format(role))
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(role, role))

current["DATABASE_URL"] = f"postgres://{app_db}:{password}@{host}:5432/{app_db}"
sm.put_secret_value(SecretId=target, SecretString=json.dumps(current))
print(f"database {app_db} ready; DATABASE_URL written to {target}")
