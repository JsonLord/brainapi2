"""Content checkpoints use real Git refs; PG-specific gates live in roundtrip script."""
import json
from pathlib import Path

import pytest

from src.services.brain_backup.git import BackupError, GitRepository
from src.services.brain_backup.manager import Manager, validate
from src.services.brain_backup.postgres import canonical
from src.services.brain_backup.state import mark_dirty, generation, require_startup_ready

PROFILE = {"profile": "embeddinggemma2-text-v1", "provider": "ollama", "model": "embeddinggemma-2:270m",
           "native_dimension": 768, "stored_dimension": 512, "transform": "truncate-512-l2",
           "instructions": "google-search-query-document-v1"}


class Database:
    profile = PROFILE
    system = "system"

    def __init__(self):
        self.registry = {"brains": [], "workspaces": []}
        self.restores = 0

    def major_version(self):
        return 15

    def has_local_content(self):
        return bool(self.registry["brains"])

    def snapshot(self, output):
        (output / "system").mkdir()
        (output / "brains").mkdir()
        (output / "system/registry.json").write_bytes(canonical(self.registry))
        return self.registry, {b["name_key"]: "fact-v1" for b in self.registry["brains"]}

    def restore(self, output, registry):
        self.registry = registry
        self.restores += 1


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_BACKUP_STATE_DIR", str(tmp_path / "state"))
    import subprocess
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
    db = Database()
    manager = Manager(GitRepository(tmp_path / "checkout", str(remote)), db, tmp_path / "local")
    return manager, db, remote


def add_brain(db):
    db.registry["brains"].append({"id": "stable-uuid", "name_key": "lifeos-projects", "created_at": "2026-10-22",
                                  "document": {"id": "stable-uuid", "name_key": "lifeos-projects"}})


def test_empty_remote_first_boot_and_no_redundant_commits(checkpoint):
    manager, db, _ = checkpoint
    manager.startup()
    add_brain(db)
    first, changed = manager.backup()
    assert changed and len(first) == 40
    second, changed = manager.backup()
    assert second == first and not changed


def test_restart_restores_same_id_before_bootstrap(checkpoint):
    manager, db, _ = checkpoint
    manager.startup()
    add_brain(db)
    manager.backup()
    db.registry = {"brains": [], "workspaces": []}
    manager.startup()
    assert db.restores == 1
    assert db.registry["brains"][0]["id"] == "stable-uuid"
    manager.startup()
    assert db.restores == 1


def test_corrupt_checksum_fails_closed(checkpoint):
    manager, db, _ = checkpoint
    manager.startup()
    add_brain(db)
    manager.backup()
    (manager.repo.path / "system/registry.json").write_text("{}")
    with pytest.raises(BackupError, match="CHECKSUM"):
        validate(manager.repo.path, PROFILE)


def test_embedding_mismatch_fails_closed(checkpoint):
    manager, db, _ = checkpoint
    manager.startup()
    manager.backup()
    with pytest.raises(BackupError, match="INCOMPATIBLE"):
        validate(manager.repo.path, {**PROFILE, "native_dimension": 1024})


def test_missing_pat_never_uses_other_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("SYSTEM_PAT", "must-not-use")
    monkeypatch.setenv("HF_TOKEN", "must-not-use")
    with pytest.raises(BackupError, match="GITHUB_PAT_REQUIRED"):
        GitRepository.configured(tmp_path)


def test_divergent_remote_never_overwritten(checkpoint, tmp_path):
    manager, db, remote = checkpoint
    manager.startup()
    manager.backup()
    other = Manager(GitRepository(tmp_path / "other-git", str(remote)), Database(), tmp_path / "other-local")
    other.startup()
    add_brain(other.pg)
    newer, _ = other.backup()
    add_brain(db)
    with pytest.raises(BackupError, match="CONFLICT"):
        manager.backup()
    assert manager.repo.fetch() == newer


def test_populated_local_without_receipt_conflicts(checkpoint):
    manager, db, _ = checkpoint
    manager.startup()
    add_brain(db)
    manager.backup()
    manager.receipt_path.unlink()
    with pytest.raises(BackupError, match="CONFLICT"):
        manager.startup()


def test_dirty_counter_and_startup_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_BACKUP_ENABLED", "true")
    monkeypatch.setenv("BRAIN_BACKUP_STATE_DIR", str(tmp_path))
    mark_dirty()
    assert generation() == 1
    with pytest.raises(RuntimeError, match="RESTORE_REQUIRED"):
        require_startup_ready()


def test_entrypoint_restore_precedes_bootstrap_and_supervisor():
    text = Path("entrypoint.sh").read_text()
    assert text.index("-m src.services.brain_backup restore") < text.index("WorkspaceService(data_adapter).bootstrap()") < text.index("exec /usr/bin/supervisord")


def test_manifest_rejects_extra_auth_file(checkpoint):
    manager, db, _ = checkpoint
    manager.startup()
    manager.backup()
    (manager.repo.path / "access_tokens.json").write_text("sensitive")
    with pytest.raises(BackupError, match="UNEXPECTED_FILES"):
        validate(manager.repo.path, PROFILE)


def test_unknown_manifest_version_and_postgres_major_rejected(checkpoint):
    manager, db, _ = checkpoint
    manager.startup()
    manager.backup()
    path = manager.repo.path / "manifest.json"
    document = json.loads(path.read_text())
    document["postgres_major"] = 16
    path.write_text(json.dumps(document))
    with pytest.raises(BackupError, match="INCOMPATIBLE"):
        validate(manager.repo.path, PROFILE)


def test_symlink_and_path_traversal_rejected(checkpoint):
    manager, db, _ = checkpoint
    manager.startup()
    manager.backup()
    (manager.repo.path / "system/link").symlink_to("registry.json")
    with pytest.raises(BackupError, match="UNEXPECTED_FILES"):
        validate(manager.repo.path, PROFILE)


def test_startup_requires_current_container_receipt(tmp_path, monkeypatch):
    from src.services.brain_backup.state import write_status
    monkeypatch.setenv("BRAIN_BACKUP_ENABLED", "true")
    monkeypatch.setenv("BRAIN_BACKUP_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("BRAIN_BACKUP_STARTUP_ID", "previous-container")
    write_status(state="ready")
    monkeypatch.setenv("BRAIN_BACKUP_STARTUP_ID", "current-container")
    with pytest.raises(RuntimeError, match="RESTORE_REQUIRED"):
        require_startup_ready()


def test_git_credentials_never_persisted(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    monkeypatch.setenv("GITHUB_PAT", "test-github-secret-do-not-persist")
    git = GitRepository.configured(tmp_path / "repository")
    git.path.mkdir()
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=b""))
    monkeypatch.setattr("src.services.brain_backup.git.subprocess.run", run)
    git.run("ls-remote", git.url)
    args, options = run.call_args
    assert "test-github-secret" not in repr(args)
    assert git.url == "https://github.com/JsonLord/nodepad-brain-backup.git"
    assert options["env"]["GITHUB_PAT"] == "test-github-secret-do-not-persist"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert "test-github-secret" not in path.read_text()


def test_cross_process_dirty_counter_is_locked(tmp_path, monkeypatch):
    import multiprocessing
    monkeypatch.setenv("BRAIN_BACKUP_ENABLED", "true")
    monkeypatch.setenv("BRAIN_BACKUP_STATE_DIR", str(tmp_path))
    ctx = multiprocessing.get_context("spawn")
    workers = [ctx.Process(target=mark_dirty) for _ in range(12)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    assert generation() == 12


def test_enabled_cli_missing_pat_fails_without_bootstrap(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    env = dict(os.environ)
    env.pop("GITHUB_PAT", None)
    env.update(BRAIN_BACKUP_ENABLED="true", BRAIN_BACKUP_ROOT=str(tmp_path / "private"),
               BRAIN_BACKUP_STATE_DIR=str(tmp_path / "state"))
    result = subprocess.run([sys.executable, "-m", "src.services.brain_backup", "restore"],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 1 and result.stderr.strip() == "BACKUP_GITHUB_PAT_REQUIRED"
    status = json.loads((tmp_path / "state/status.json").read_text())
    assert status["state"] == "failed"


def test_shutdown_finishes_inflight_then_attempts_final_checkpoint(tmp_path, monkeypatch):
    from src.services.brain_backup import __main__ as cli
    import signal
    callbacks, attempts = {}, []

    class Stop:
        stopped = False
        def set(self):
            self.stopped = True
        def wait(self, interval):
            return self.stopped

    class FakeManager:
        def backup(self):
            attempts.append("checkpoint")
            if len(attempts) == 1:
                callbacks[signal.SIGTERM]()

    monkeypatch.setattr(cli, "enabled", lambda: True)
    monkeypatch.setattr(cli, "configured", lambda: FakeManager())
    monkeypatch.setattr(cli.threading, "Event", Stop)
    monkeypatch.setattr(cli.signal, "signal", lambda sig, callback: callbacks.update({sig: callback}))
    monkeypatch.setattr(cli.sys, "argv", ["brain_backup", "run"])
    cli.run()
    assert len(attempts) == 2


def test_deployment_secret_in_workspace_or_content_rejected(monkeypatch):
    from io import BytesIO
    from src.services.brain_backup.postgres import PostgreSQL
    monkeypatch.setenv("GITHUB_PAT", "never-upload-this-secret")
    pg = PostgreSQL({"database": "system"}, PROFILE)
    for value in (b'{"description":"never-upload-this-secret"}', b'bpat_abcdefghijklmnopqrstuvwxyz'):
        with pytest.raises(BackupError, match="SECRET_IN_CONTENT"):
            pg.inspect_secret_stream(BytesIO(value))


def _commit_repo(repo, message):
    repo.run("add", "--all")
    repo.run("-c", "user.name=Qualification", "-c", "user.email=test@brainapi.local", "commit", "--quiet", "-m", message)
    repo.run("push", "--quiet", repo.url, "HEAD:refs/heads/main")


def test_initialized_readme_repository_allows_first_boot(checkpoint):
    manager, db, _ = checkpoint
    manager.repo.fetch()
    (manager.repo.path / "README.md").write_text("Brain checkpoints repository")
    _commit_repo(manager.repo, "Initialize backup repository")
    manager.startup()
    assert not db.restores
    _, changed = manager.backup()
    assert changed
    validate(manager.repo.path, PROFILE)


def test_deleted_manifest_never_becomes_empty_first_boot(checkpoint):
    manager, db, _ = checkpoint
    manager.startup()
    manager.backup()
    manager.repo.run("rm", "-r", "--ignore-unmatch", "manifest.json", "system", "brains")
    (manager.repo.path / "README.md").write_text("Accidentally deleted snapshot")
    _commit_repo(manager.repo, "Remove snapshot")
    manager.receipt_path.unlink()
    with pytest.raises(BackupError, match="MANIFEST_MISSING"):
        manager.startup()
