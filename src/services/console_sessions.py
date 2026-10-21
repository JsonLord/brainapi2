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
        principal = service.revalidate(principal)
        return principal, record["csrf"]

    def revoke(self, cookie):
        if cookie:
            self.redis.delete(self._key(cookie))


@lru_cache(maxsize=1)
def get_console_sessions():
    from src.lib.redis.client import RedisClient
    return ConsoleSessions(RedisClient().client)
