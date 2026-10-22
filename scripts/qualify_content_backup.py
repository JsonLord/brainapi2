"""Real logical restore round-trip against an explicitly disposable PG cluster.

Set BACKUP_QUALIFICATION_DISPOSABLE=true and the normal HF CPU environment.
Never run against the production cluster. Only randomly named test DBs are
created/dropped. Git remote is a local bare repository; no deployment occurs.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def qualify():
    if os.getenv("BACKUP_QUALIFICATION_DISPOSABLE") != "true":
        raise RuntimeError("Disposable PostgreSQL cluster acknowledgement required")
    # Set before application/config imports; never touch a real registry.
    suffix = uuid4().hex[:12]
    system = "backup_qualification_" + suffix
    os.environ["POSTGRES_SYSTEM_DATABASE"] = system
    os.environ["BRAIN_BACKUP_ENABLED"] = "true"
    from src.config import config
    from src.constants.data import TextChunk, Workspace
    from src.lib.postgresql.data import PostgreSQLDataClient
    from src.lib.postgresql._naming import brain_db_name
    from src.lib.postgresql import _provisioning as pools
    from src.services.brain_backup.git import GitRepository, BackupError
    from src.services.brain_backup.postgres import PostgreSQL
    from src.services.brain_backup.manager import Manager, validate
    from src.services.brain_backup.state import generation
    from src.lib.postgresql.vectors import PostgreSQLVectorStoreClient
    from psycopg2 import sql
    from psycopg2.extras import Json
    pg = PostgreSQL.configured()
    names = ["backup-projects-" + suffix, "backup-habits-" + suffix]
    orphan = "backup-orphan-" + suffix
    facts = ["Asterquartz project launches on October 31 with purple balloons.",
             "Asterquartz habit is twelve minutes of sunrise breathing each morning."]
    results = {}
    with tempfile.TemporaryDirectory(prefix="brainapi-backup-roundtrip-") as temp:
        temp = Path(temp)
        os.environ["BRAIN_BACKUP_STATE_DIR"] = str(temp / "state")
        remote = temp / "remote.git"
        subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
        manager = Manager(GitRepository(temp / "checkout", str(remote)), pg, temp / "local")
        client = PostgreSQLDataClient()
        # Prove every cleanup target was absent before testing. Never delete a
        # preexisting database, even in a cluster acknowledged as disposable.
        with pg.connect("postgres") as conn, conn.cursor() as cur:
            targets = [system, *(brain_db_name(n) for n in names + [orphan])]
            cur.execute("SELECT datname FROM pg_database WHERE datname=ANY(%s)", (targets,))
            if cur.fetchall():
                raise RuntimeError("Qualification namespace collision; no cleanup attempted")
        try:
            with pg.connect("template1", autocommit=True) as conn, conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            client._ensure_system_schema()
            manager.startup()
            identities = {}
            for name, fact in zip(names, facts):
                brain = client.create_brain(name)
                identities[name] = brain.id
                client.create_workspace(Workspace(brain_id=name, slug=name, display_name=name))
                client.save_text_chunk(TextChunk(text=fact), brain_id=name)
                vector = PostgreSQLVectorStoreClient()
                for store in ("nodes", "triplets", "observations", "data", "relationships"):
                    vector._ensure_store(store, name)
            # Exercise legacy PAT stripping and separate access-token registry.
            with pg.connect() as conn, conn.cursor() as cur:
                cur.execute("UPDATE data_brains SET pat='legacy-secret-not-for-backup', document=document || %s::jsonb",
                            (Json({"pat": "legacy-secret-not-for-backup"}),))
                cur.execute("CREATE TABLE access_tokens(token_hash text)")
                cur.execute("INSERT INTO access_tokens VALUES('separate-access-token-hash')")
                conn.commit()
            baseline = generation()
            with pools.borrow(pools.get_system_pool()) as conn, conn.cursor() as cur:
                cur.execute("INSERT INTO access_tokens VALUES('auth-only-not-content')")
                conn.commit()
            assert generation() == baseline
            with pools.borrow(pools.get_brain_pool(names[0])) as conn, conn.cursor() as cur:
                cur.execute("UPDATE data_text_chunks SET text='rolled back'")
                conn.rollback()
            assert generation() == baseline
            with pools.borrow(pools.get_brain_pool(names[0])) as conn:
                with conn, conn.cursor() as cur:
                    cur.execute("UPDATE data_text_chunks SET metadata=metadata")
            assert generation() > baseline
            results["dirty_tracking"] = "auth-only writes and rollback excluded; context-managed content COMMIT tracked"
            with pg.connect("postgres", autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(brain_db_name(orphan))))
            assert generation() > 0
            first, changed = manager.backup()
            assert changed
            manifest, registry = validate(manager.repo.path, pg.profile, pg.major_version())
            assert set(manifest["brains"]) == set(names) and orphan not in manifest["brains"]
            assert identities == {b["name_key"]: b["id"] for b in registry["brains"]}
            assert "legacy-secret" not in json.dumps(registry)
            assert not any("access_tokens" in p for p in manifest["checksums"])
            second, changed = manager.backup()
            assert second == first and not changed
            results["logical_snapshot"] = "registered brains only; five 512d profiles; no redundant commit"
            results["credential_exclusion"] = "legacy PAT removed; access_tokens table/hash excluded"
            # Force profile rejection on a real initialized vector DB.
            with pg.connect(brain_db_name(names[0])) as conn, conn.cursor() as cur:
                cur.execute("UPDATE embedding_profiles SET profile=profile || '{\"native_dimension\":1024}'::jsonb")
                conn.commit()
            try:
                manager.backup()
            except BackupError as exc:
                assert "PROFILE_MISMATCH" in str(exc)
            else:
                raise AssertionError("Mismatched vector metadata was accepted")
            results["profile_mismatch"] = "rejected"
            # Only names created above, on the acknowledged disposable cluster.
            for database in list(pools._registry._pools):
                pools._registry.discard(database)
            with pg.connect("postgres", autocommit=True) as conn, conn.cursor() as cur:
                for name in names:
                    cur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(brain_db_name(name))))
            with pg.connect() as conn, conn.cursor() as cur:
                cur.execute("TRUNCATE data_workspaces,data_brains")
                conn.commit()
            manager.startup()
            restored = PostgreSQLDataClient()
            for name, fact in zip(names, facts):
                assert restored.get_brain(name).id == identities[name]
                assert fact in [chunk.text for chunk, _ in restored.search_bm25("Asterquartz", name)]
                assert restored.get_workspace_by_brain_id(name).brain_id == name
            results["roundtrip"] = "destroyed test brain DBs, restored same IDs/workspaces/facts and BM25 results"
            restored.save_text_chunk(TextChunk(text="Asterquartz newer local content retained on restart."), names[0])
            manager.startup()
            assert any("newer local content" in c.text for c, _ in restored.search_bm25("Asterquartz", names[0]))
            results["restart"] = "nonempty local data retained with matching checkpoint lineage"
            if os.getenv("BACKUP_QUALIFICATION_RETRIEVAL") == "true":
                from src.services.api.controllers.search import search
                from src.services.api.controllers.retrieve import get_context
                from src.services.api.constants.requests import SearchRequestBody, GetContextRequestBody
                for name, fact in zip(names, facts):
                    hits = asyncio.run(search(SearchRequestBody(query="Asterquartz", brain_id=name)))
                    context = asyncio.run(get_context(GetContextRequestBody(text="Asterquartz", brain_id=name)))
                    assert fact in json.dumps(hits.model_dump(mode="json"))
                    assert fact in json.dumps(context.model_dump(mode="json"))
                results["search_context"] = "actual search/context controllers returned both restored unique facts"
            # New content changes the remote commit after restoration.
            restored.save_text_chunk(TextChunk(text="Asterquartz new milestone after restoration."), names[0])
            third, changed = manager.backup()
            assert changed and third != first
            results["new_mutation"] = "new logical checkpoint commit produced"
            export = os.getenv("BACKUP_QUALIFICATION_EXPORT")
            if export:
                import shutil
                shutil.copytree(remote, Path(export))
        finally:
            for database in list(pools._registry._pools):
                pools._registry.discard(database)
            with pg.connect("postgres", autocommit=True) as conn, conn.cursor() as cur:
                for database in [*(brain_db_name(n) for n in names + [orphan]), system]:
                    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
    print(json.dumps({"result": "passed", **results}, sort_keys=True))


if __name__ == "__main__":
    qualify()
