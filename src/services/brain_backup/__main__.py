"""`python -m src.services.brain_backup restore|run` (entrypoint integration)."""
import os
import signal
import sys
import threading
import logging
from pathlib import Path

from .git import BackupError, GitRepository
from .manager import Manager
from .postgres import PostgreSQL
from .state import enabled, write_status


def configured():
    root = Path(os.getenv("BRAIN_BACKUP_ROOT", str(Path(os.getenv("PERSISTENT_ROOT", "/app/.data/brainapi")) / "content-backup")))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    # Validate credentials before importing/initializing application databases.
    repository = GitRepository.configured(root / "repository")
    return Manager(repository, PostgreSQL.configured(), root)


def run():
    if not enabled():
        return
    try:
        manager = configured()
        if sys.argv[1:] == ["restore"]:
            from src.lib.postgresql.data import PostgreSQLDataClient
            PostgreSQLDataClient()._ensure_system_schema()
            manager.startup()
            return
        if sys.argv[1:] == ["verify-startup"]:
            manager.verify_startup()
            return
        if sys.argv[1:] != ["run"]:
            raise BackupError("BACKUP_INVALID_COMMAND")
        interval = int(os.getenv("BRAIN_BACKUP_INTERVAL_SECONDS", "300"))
        if interval < 1:
            raise BackupError("BACKUP_INVALID_INTERVAL")
        stop = threading.Event()
        # Bound the final snapshot, including all tools and lock acquisition.
        def terminate(*_):
            stop.set()
        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)
        def attempt():
            try:
                from .state import locked, state_root
                with locked(state_root() / "barrier.lock"):
                    manager.backup()
            except Exception as exc:
                code = str(exc) if isinstance(exc, BackupError) else "BACKUP_CHECKPOINT_FAILED"
                write_status(state="degraded", error=code)
                if code in {"BACKUP_REMOTE_CONFLICT", "BACKUP_LOCAL_REMOTE_CONFLICT"}:
                    from .maintenance import fail
                    fail(code)
                logging.error("%s", code)

        while not stop.wait(interval):
            attempt()
        # If SIGTERM arrived during an in-flight snapshot, finish that attempt
        # and then capture writes committed before Supervisor stopped writers.
        attempt()
    except Exception as exc:
        code = str(exc) if isinstance(exc, BackupError) else "BACKUP_STARTUP_FAILED"
        write_status(state="failed", error=code)
        print(code, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
