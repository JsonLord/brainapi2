"""Non-secret storage state and explicit production durability gate."""

import os
from pathlib import Path


def persistence_state(environ=None):
    env = os.environ if environ is None else environ
    root = env.get("BRAINAPI_DURABLE_ROOT", "/data")
    durable = Path(root).is_dir()
    mode = "durable" if durable else "ephemeral"
    configured = env.get("PERSISTENCE_MODE")
    if configured and configured != mode:
        raise ValueError("PERSISTENCE_MODE disagrees with the available durable root")
    return {"mode": mode, "root": str(Path(root) / "brainapi") if durable else "/app/.data/brainapi",
            "production_ready": durable}


def enforce_persistence():
    state = persistence_state()
    if (state["mode"] == "ephemeral" and os.getenv("ENV", "production") != "development"
            and os.getenv("ALLOW_EPHEMERAL_STORAGE", "false").lower() != "true"):
        raise RuntimeError("EPHEMERAL_STORAGE_REQUIRES_OVERRIDE: configure durable storage or explicitly allow staging")
    return state
