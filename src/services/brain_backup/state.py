"""Cross-process dirty generation and public, credential-free backup status."""
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path


def enabled():
    return os.getenv("BRAIN_BACKUP_ENABLED", "false").lower() == "true"


def state_root():
    return Path(os.getenv("BRAIN_BACKUP_STATE_DIR", "/tmp/brainapi-backup-state"))


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        yield stream


def generation():
    with locked(state_root() / "dirty") as stream:
        return int(stream.read() or "0")


def mark_dirty():
    if not enabled():
        return
    # Called after COMMIT: do not turn a committed mutation into a false HTTP 500.
    try:
        with locked(state_root() / "dirty") as stream:
            value = int(stream.read() or "0") + 1
            stream.seek(0)
            stream.truncate()
            stream.write(str(value))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        import logging
        logging.getLogger(__name__).error("BACKUP_DIRTY_MARK_FAILED; periodic reconciliation remains active")


def write_status(**fields):
    path = state_root() / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        prior = json.loads(path.read_text())
    except (OSError, ValueError):
        prior = {}
    startup_id = os.getenv("BRAIN_BACKUP_STARTUP_ID")
    if prior.get("startup_id") != startup_id:
        prior = {}
    prior.update(fields, startup_id=startup_id)
    if prior.get("state") == "ready":
        prior.pop("error", None)
    temp.write_text(json.dumps(prior, sort_keys=True))
    temp.replace(path)


def public_status():
    if not enabled():
        return {"enabled": False}
    try:
        value = json.loads((state_root() / "status.json").read_text())
        return {"enabled": True, **value}
    except (OSError, ValueError):
        return {"enabled": True, "state": "not_ready"}


def require_startup_ready():
    if not enabled():
        return
    status = public_status()
    if not os.getenv("BRAIN_BACKUP_STARTUP_ID") or status.get("startup_id") != os.getenv("BRAIN_BACKUP_STARTUP_ID") or status.get("state") not in {"ready", "degraded"}:
        raise RuntimeError("BACKUP_STARTUP_RESTORE_REQUIRED")
