"""Opaque Redis sessions contain identities, never submitted access credentials."""

import json
import os
import secrets
from dataclasses import asdict
from functools import lru_cache

from src.services.auth import AuthError, Principal, token_digest
from src.services.auth_config import resolve_system_pat

SESSION_COOKIE = "__Host-brainapi-session"


class ConsoleSessions:
    def __init__(self, redis):
        self.redis = redis

    def _key(self, cookie):
        return "system:console-session:" + token_digest(cookie)

    def create(self, principal):
        cookie = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        ttl = max(300, min(int(os.getenv("CONSOLE_SESSION_TTL_SECONDS", "28800")), 86400))
        record = asdict(principal)
        record["permissions"] = sorted(principal.permissions)
        self.redis.set(self._key(cookie), json.dumps({"principal": record, "csrf": csrf}), ex=ttl)
        return cookie, csrf, ttl

    def resolve(self, cookie, service):
        if not cookie:
            raise AuthError()
        raw = self.redis.get(self._key(cookie))
        if not raw:
            raise AuthError()
        record = json.loads(raw)
        snapshot = record["principal"]
        snapshot["permissions"] = frozenset(snapshot["permissions"])
        principal = Principal(**snapshot)
        # Recheck the originating credential on EVERY request: revocation and
        # expiry must also terminate browser sessions, not just agent access.
        if principal.is_system:
            canonical = resolve_system_pat()
            if not canonical or not secrets.compare_digest(principal.credential_hash or "", token_digest(canonical)):
                raise AuthError()
        elif principal.token_id:
            row = service.token_store.get(principal.token_id)
            if not row:
                raise AuthError()
            principal = service._token_principal(row)
        else:
            brain = service.data.get_brain(principal.brain_id)
            if (not brain or not brain.pat or brain.id != principal.subject_id
                    or not secrets.compare_digest(token_digest(brain.pat), principal.credential_hash or "")):
                raise AuthError()
        return principal, record["csrf"]

    def revoke(self, cookie):
        if cookie:
            self.redis.delete(self._key(cookie))


@lru_cache(maxsize=1)
def get_console_sessions():
    from src.lib.redis.client import RedisClient
    return ConsoleSessions(RedisClient().client)
