"""Explicit credentials in the system PostgreSQL registry; hashes only at rest."""

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4
import threading
import secrets

import psycopg2.extras

from src.services.auth import AuthError, token_digest

PERMISSIONS = frozenset({"memory:read", "memory:write", "brain:read", "brain:write"})
_DDL = """
CREATE TABLE IF NOT EXISTS access_tokens (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    brain_id TEXT NOT NULL,
    brain_resource_id TEXT NOT NULL,
    token_prefix TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    permissions TEXT[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    legacy BOOLEAN NOT NULL DEFAULT FALSE,
    created_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS access_tokens_brain ON access_tokens (brain_id);
"""


def public_metadata(row):
    return {key: value for key, value in row.items() if key not in {"token_hash", "brain_resource_id"}}


class PostgreSQLTokenRepository:
    def __init__(self):
        self._ready = False
        self._lock = threading.Lock()

    @contextmanager
    def connection(self):
        from src.lib.postgresql._provisioning import borrow, get_system_pool
        with borrow(get_system_pool()) as connection:
            yield connection

    def initialize(self):
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with self.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(_DDL)
                conn.commit()
            self._ready = True

    def _query(self, sql, args=(), many=False, write=False):
        self.initialize()
        with self.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, args)
                rows = cur.fetchall() if many else cur.fetchone()
            if write:
                conn.commit()
        return rows

    def find_by_hash(self, digest):
        return self._query("SELECT * FROM access_tokens WHERE token_hash=%s", (digest,))

    def get(self, token_id):
        return self._query("SELECT * FROM access_tokens WHERE id=%s", (token_id,))

    def list(self, brain_id=None):
        sql = "SELECT * FROM access_tokens"
        args = ()
        if brain_id is not None:
            sql += " WHERE brain_id=%s"
            args = (brain_id,)
        return [public_metadata(row) for row in self._query(sql + " ORDER BY created_at DESC", args, many=True)]

    def create(self, *, brain, label, permissions, created_by, expires_at=None):
        if not permissions or not set(permissions) <= PERMISSIONS:
            raise ValueError("Unsupported token permissions")
        if expires_at and expires_at <= datetime.now(timezone.utc):
            raise ValueError("Token expiry must be in the future")
        plaintext = "bpat_" + secrets.token_urlsafe(32)
        row = self._query("""
            INSERT INTO access_tokens
                (id,label,brain_id,brain_resource_id,token_prefix,token_hash,permissions,created_by,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """, (str(uuid4()), label, brain.name_key, brain.id, plaintext[:12],
                   token_digest(plaintext), sorted(set(permissions)), created_by, expires_at), write=True)
        return {**public_metadata(row), "token": plaintext}

    def revoke(self, token_id):
        row = self._query("UPDATE access_tokens SET revoked_at=COALESCE(revoked_at,NOW()) WHERE id=%s RETURNING *",
                          (token_id,), write=True)
        if not row:
            raise AuthError(404, "TOKEN_NOT_FOUND")
        return public_metadata(row)

    def touch(self, token_id):
        self._query("UPDATE access_tokens SET last_used_at=NOW() WHERE id=%s RETURNING id", (token_id,), write=True)


token_repository = PostgreSQLTokenRepository()
