"""Peer-authenticated startup provisioning, without credential-bearing shell SQL."""
import os
import pwd
import sys

import psycopg2
from psycopg2 import sql


def provision():
    postgres = pwd.getpwnam("postgres")
    if os.geteuid() == 0:
        os.initgroups(postgres.pw_name, postgres.pw_gid)
        os.setgid(postgres.pw_gid)
        os.setuid(postgres.pw_uid)
    role = os.environ["POSTGRES_USERNAME"]
    database = os.environ["POSTGRES_SYSTEM_DATABASE"]
    conn = psycopg2.connect(host="/tmp", dbname="postgres", user="postgres")
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            # Suppress SQL text even on an unexpected error: ALTER ROLE contains
            # a credential. This affects only this administrative connection.
            cur.execute("SET log_statement='none'")
            cur.execute("SET log_min_error_statement='panic'")
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
            cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN CREATEDB PASSWORD %s").format(sql.Identifier(role)),
                        (os.environ["POSTGRES_PASSWORD"],))
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(role)))
            cur.execute(sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(role)))
    finally:
        conn.close()
    conn = psycopg2.connect(host="/tmp", dbname=database, user="postgres")
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        provision()
    except Exception:
        print("POSTGRES_PROVISIONING_FAILED", file=sys.stderr)
        raise SystemExit(1) from None
