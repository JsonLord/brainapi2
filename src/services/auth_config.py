"""Resolve the deployment administrator credential once, without safe defaults."""

import logging
import os
import secrets
from functools import lru_cache


@lru_cache(maxsize=1)
def _warn_legacy_alias():
    logging.getLogger(__name__).warning(
        "BRAINPAT_TOKEN is deprecated; configure SYSTEM_PAT instead."
    )


def resolve_system_pat(environ=None) -> str | None:
    env = os.environ if environ is None else environ
    canonical = env.get("SYSTEM_PAT") or None
    legacy = env.get("BRAINPAT_TOKEN") or None
    if canonical and legacy and not secrets.compare_digest(canonical, legacy):
        raise ValueError("SYSTEM_PAT_CONFLICT: configure one administrator credential")
    if not canonical and legacy:
        _warn_legacy_alias()
    resolved = canonical or legacy
    if resolved == "brainpat_default_token":
        raise ValueError("SYSTEM_PAT_INVALID: default administrator credentials are forbidden")
    if not resolved and env.get("ENV", "production").lower() != "development":
        raise ValueError("SYSTEM_PAT_REQUIRED: configure an administrator credential")
    return resolved
