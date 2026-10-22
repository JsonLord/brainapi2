"""Logical PostgreSQL snapshots with an explicit content-table allowlist."""
import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json

from .git import BackupError

TABLES = frozenset({
    "data_text_chunks", "data_observations", "data_structured_data", "data_kg_changes",
    "kg_nodes", "kg_relationships", "kg_hub_bridges", "kg_topic_sessions", "embedding_profiles",
    "vectors_nodes", "vectors_triplets", "vectors_observations", "vectors_data", "vectors_relationships",
})
STORES = {"nodes", "triplets", "observations", "data", "relationships"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class PostgreSQL:
    def __init__(self, settings, profile):
        self.settings = settings
        self.profile = profile
        self.system = settings.get("system_database") or settings["database"]

    @classmethod
    def configured(cls):
        from src.config import config
        from src.lib.embeddings.profile import index_profile
        if config.data_db != "postgresql" or config.vector_db != "postgresql" or config.graph_db != "networkx":
            raise BackupError("BACKUP_REQUIRES_POSTGRESQL_NETWORKX")
        from src.constants.embeddings import EMBEDDING_STORES_SIZES
        if set(EMBEDDING_STORES_SIZES.values()) != {512}:
            raise BackupError("BACKUP_REQUIRES_UNIFORM_512_DIMENSIONS")
        p = config.postgresql
        return cls(dict(host=p.host, port=p.port, user=p.username, password=p.password,
                        database=p.database, system_database=getattr(p, "system_database", None)), index_profile(config, 512))

    @contextmanager
    def connect(self, database=None, autocommit=False):
        args = {k: v for k, v in self.settings.items() if k not in {"database", "system_database"}}
        conn = psycopg2.connect(**args, dbname=database or self.system)
        try:
            conn.autocommit = autocommit
            yield conn
        finally:
            conn.close()

    def tool(self, program, database, *args, stdout=None):
        env = os.environ.copy()
        env.update(PGHOST=str(self.settings["host"]), PGPORT=str(self.settings["port"]),
                   PGUSER=self.settings["user"], PGPASSWORD=self.settings["password"], PGDATABASE=database)
        try:
            result = subprocess.run([program, *args], env=env, stdout=stdout or subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            raise BackupError("BACKUP_POSTGRES_TOOL_UNAVAILABLE") from None
        if result.returncode:
            raise BackupError("BACKUP_POSTGRES_TOOL_FAILED")
        return result.stdout

    def major_version(self):
        with self.connect() as conn:
            return conn.server_version // 10000

    def inspect_secret_stream(self, stream):
        # Known deployment credentials and minted Brain PATs must never enter
        # registry metadata or content archives, even if pasted into documents.
        import re
        candidates = [os.getenv(key, "").encode() for key in (
            "SYSTEM_PAT", "BRAINPAT_TOKEN", "GITHUB_PAT", "HF_TOKEN",
            "POSTGRES_PASSWORD", "REDIS_PASSWORD")]
        candidates = [v for v in candidates if len(v) >= 8]
        tail = b""
        overlap = max([len(v) for v in candidates] + [256])
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            data = tail + chunk
            if any(secret in data for secret in candidates) or re.search(rb"\bbpat_[A-Za-z0-9_-]{16,}", data):
                raise BackupError("BACKUP_SECRET_IN_CONTENT")
            tail = data[-overlap:]

    def inspect_secrets(self, archive, database):
        # Spool logical SQL rather than retain large plaintext in memory.
        import tempfile
        with tempfile.TemporaryFile() as stream:
            self.tool("pg_restore", database, "--file=-", str(archive), stdout=stream)
            stream.seek(0)
            self.inspect_secret_stream(stream)

    def registry(self):
        from src.constants.data import Brain, Workspace
        from src.services.brain_identity import validate_brain_id
        from src.lib.postgresql._naming import brain_db_name
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id,name_key,document,created_at::text FROM data_brains ORDER BY name_key")
            brains = []
            for identity, name, document, created in cur.fetchall():
                validate_brain_id(name)
                # Explicit whitelist, never export legacy PAT/document credentials.
                if document.get("id") != identity or document.get("name_key") != name:
                    raise BackupError("BACKUP_REGISTRY_IDENTITY_MISMATCH")
                brains.append({"id": identity, "name_key": name, "created_at": created,
                               "document": Brain(id=identity, name_key=name).model_dump(mode="json")})
            physical = [brain_db_name(b["name_key"]) for b in brains]
            if len(set(physical)) != len(physical):
                raise BackupError("BACKUP_DATABASE_NAME_COLLISION")
            cur.execute("SELECT id,brain_id,slug,document,created_at::text FROM data_workspaces ORDER BY id")
            workspaces = []
            names = {b["name_key"] for b in brains}
            for identity, name, slug, document, created in cur.fetchall():
                if name not in names:
                    continue
                obj = Workspace.model_validate(document)
                if (obj.id, obj.brain_id, obj.slug) != (identity, name, slug):
                    raise BackupError("BACKUP_WORKSPACE_IDENTITY_MISMATCH")
                workspaces.append({"id": identity, "brain_id": name, "slug": slug,
                                   "document": obj.model_dump(mode="json"), "created_at": created})
            return {"brains": brains, "workspaces": workspaces}

    def tables(self, cur):
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        tables = [r[0] for r in cur.fetchall()]
        # Unknown tables could contain credentials OR unmodelled content. Refuse
        # instead of silently losing it. Never discover unregistered databases.
        if set(tables) - TABLES:
            raise BackupError("BACKUP_UNKNOWN_BRAIN_TABLE")
        return tables

    def validate_profile(self, cur, tables):
        profiles = {}
        if "embedding_profiles" in tables:
            cur.execute("SELECT store,profile FROM embedding_profiles ORDER BY store")
            profiles = dict(cur.fetchall())
            if set(profiles) - STORES or any(p != self.profile for p in profiles.values()):
                raise BackupError("BACKUP_EMBEDDING_PROFILE_MISMATCH")
        for table in tables:
            if table.startswith("vectors_"):
                store = table.removeprefix("vectors_")
                cur.execute("SELECT atttypmod FROM pg_attribute WHERE attrelid=%s::regclass AND attname='embeddings'", (table,))
                row = cur.fetchone()
                if not row or row[0] != self.profile["stored_dimension"] or store not in profiles:
                    raise BackupError("BACKUP_EMBEDDING_PROFILE_MISMATCH")
        return profiles

    def snapshot(self, output):
        from src.lib.postgresql._naming import brain_db_name
        output = Path(output)
        (output / "system").mkdir(parents=True)
        (output / "brains").mkdir()
        registry = self.registry()
        import io
        self.inspect_secret_stream(io.BytesIO(canonical(registry)))
        (output / "system/registry.json").write_bytes(canonical(registry))
        fingerprints = {}
        for brain in registry["brains"]:
            name = brain["name_key"]
            database = brain_db_name(name)
            with self.connect(database) as conn, conn.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cur.execute("SELECT pg_export_snapshot()")
                snapshot = cur.fetchone()[0]
                tables = self.tables(cur)
                self.validate_profile(cur, tables)
                digest = hashlib.sha256()
                for table in tables:
                    digest.update(table.encode())
                    # Canonical row order makes no-op checks independent of VACUUM,
                    # heap order and pg_dump's archive timestamp.
                    with conn.cursor(name=f"backup_{table}") as rows:
                        rows.execute(sql.SQL("SELECT to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text COLLATE \"C\"").format(sql.Identifier(table)))
                        for (row,) in rows:
                            digest.update(row.encode() + b"\n")
                table_args = [item for table in tables for item in ("--table", f"public.{table}")]
                if tables:
                    archive = output / "brains" / f"{name}.dump"
                    with archive.open("wb") as stream:
                        self.tool("pg_dump", database, "--format=custom", "--no-owner", "--no-privileges",
                                  f"--snapshot={snapshot}", *table_args, stdout=stream)
                    self.tool("pg_restore", database, "--list", str(archive))
                    self.inspect_secrets(archive, database)
                    # Include schema/index changes in semantic identity, without
                    # archive timestamps or pg_dump's random restriction nonce.
                    schema = self.tool("pg_dump", database, "--schema-only", "--no-owner", "--no-privileges",
                                       f"--snapshot={snapshot}", *table_args)
                    stable = b"\n".join(line for line in schema.splitlines()
                                          if not line.startswith((b"--", b"\\restrict", b"\\unrestrict")))
                    digest.update(stable)
                fingerprints[name] = digest.hexdigest()
        if self.registry() != registry:
            raise BackupError("BACKUP_REGISTRY_CHANGED_DURING_SNAPSHOT")
        return registry, fingerprints

    def has_local_content(self):
        return bool(self.registry()["brains"])

    def restore(self, source, registry):
        from src.lib.postgresql._naming import brain_db_name
        if self.has_local_content():
            raise BackupError("BACKUP_RESTORE_REQUIRES_EMPTY_REGISTRY")
        # Preflight ALL destination collisions before writing any DB. Orphans
        # are never resurrected or overwritten, even if their names match.
        names = [brain_db_name(b["name_key"]) for b in registry["brains"]]
        if len(set(names)) != len(names):
            raise BackupError("BACKUP_DATABASE_NAME_COLLISION")
        created = []
        try:
            with self.connect("postgres", autocommit=True) as admin, admin.cursor() as cur:
                cur.execute("SELECT datname FROM pg_database WHERE datname=ANY(%s)", (names,))
                if cur.fetchall():
                    raise BackupError("BACKUP_RESTORE_DATABASE_COLLISION")
                for brain, database in zip(registry["brains"], names):
                    cur.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template1").format(sql.Identifier(database)))
                    created.append(database)
                    # Selective dumps do not include pgvector extension DDL.
                    with self.connect(database, autocommit=True) as conn, conn.cursor() as target:
                        target.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    archive = Path(source) / "brains" / f"{brain['name_key']}.dump"
                    if archive.exists():
                        self.tool("pg_restore", database, "--exit-on-error", "--single-transaction",
                                  "--no-owner", "--no-privileges", "--dbname", database, str(archive))
                    with self.connect(database) as conn, conn.cursor() as target:
                        self.validate_profile(target, self.tables(target))
            # Publish canonical registry LAST in one transaction. API is still
            # stopped; any restore failure leaves no partially registered brain.
            with self.connect() as conn, conn.cursor() as cur:
                cur.execute("LOCK TABLE data_brains,data_workspaces IN ACCESS EXCLUSIVE MODE")
                cur.execute("SELECT count(*) FROM data_brains")
                if cur.fetchone()[0]:
                    raise BackupError("BACKUP_RESTORE_REQUIRES_EMPTY_REGISTRY")
                cur.execute("DELETE FROM data_workspaces")
                for b in registry["brains"]:
                    cur.execute("INSERT INTO data_brains(id,name_key,pat,document,created_at) VALUES(%s,%s,'',%s,%s)",
                                (b["id"], b["name_key"], Json(b["document"]), b["created_at"]))
                for w in registry["workspaces"]:
                    cur.execute("INSERT INTO data_workspaces(id,brain_id,slug,document,created_at) VALUES(%s,%s,%s,%s,%s)",
                                (w["id"], w["brain_id"], w["slug"], Json(w["document"]), w["created_at"]))
                conn.commit()
        except BaseException:
            with self.connect("postgres", autocommit=True) as admin, admin.cursor() as cur:
                for database in reversed(created):
                    cur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            raise
