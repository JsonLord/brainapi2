"""Shared identities and authorization for HTTP, MCP, and browser sessions."""

from dataclasses import dataclass, field
from hashlib import sha256
import logging
import secrets
import time

from src.services.auth_config import resolve_system_pat

_legacy_warning_at = 0


class AuthError(Exception):
    def __init__(self, status=401, code="AUTH_INVALID"):
        self.status = status
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Principal:
    kind: str
    subject_id: str
    brain_id: str | None
    permissions: frozenset[str]
    token_id: str | None = None
    legacy: bool = False
    credential_hash: str | None = field(default=None, repr=False)

    @property
    def is_system(self):
        return self.kind == "system" and "system:admin" in self.permissions

    def public(self):
        return dict(authenticated=True, kind=self.kind, is_system=self.is_system,
                    brain_id=self.brain_id, permissions=sorted(self.permissions), legacy=self.legacy)


def token_digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(self, data, token_store=None):
        self.data = data
        self.token_store = token_store
        self._legacy_warning_at = 0

    def authenticate_token(self, token: str | None) -> Principal:
        global _legacy_warning_at
        if isinstance(token, Principal):
            return self.revalidate(token)
        if not token:
            raise AuthError()
        canonical = resolve_system_pat()
        if canonical and secrets.compare_digest(token, canonical):
            return Principal("system", "system", None, frozenset({"system:admin"}),
                             credential_hash=token_digest(canonical))
        # New credentials have an unambiguous namespace and are never legacy PATs.
        if token.startswith("bpat_") and self.token_store is not None:
            row = self.token_store.find_by_hash(token_digest(token))
            if row:
                principal = self._token_principal(row)
                self.token_store.touch(row["id"])
                return principal
            raise AuthError()
        brain = self.data.get_brain_by_pat(token)
        if brain and brain.pat and secrets.compare_digest(token, brain.pat):
            if time.monotonic() - _legacy_warning_at > 300:
                logging.getLogger(__name__).warning("Legacy Brain credential used; rotate to a named access token.")
                _legacy_warning_at = time.monotonic()
            return Principal("access_token", brain.id, brain.name_key,
                             frozenset({"memory:read", "memory:write", "brain:read"}),
                             legacy=True, credential_hash=token_digest(token))
        if self.token_store is not None:
            row = self.token_store.find_by_hash(token_digest(token))
            if row:
                principal = self._token_principal(row)
                self.token_store.touch(row["id"])
                return principal
        raise AuthError()

    def revalidate(self, principal):
        if principal.is_system:
            canonical = resolve_system_pat()
            if canonical and secrets.compare_digest(token_digest(canonical), principal.credential_hash or ""):
                return principal
        elif principal.token_id:
            row = self.token_store.get(principal.token_id)
            if row:
                return self._token_principal(row)
        else:
            brain = self.data.get_brain(principal.brain_id)
            if (brain and brain.id == principal.subject_id and brain.pat
                    and secrets.compare_digest(token_digest(brain.pat), principal.credential_hash or "")):
                return principal
        raise AuthError()

    def _token_principal(self, row):
        if row["revoked_at"] or (row["expires_at"] and row["expires_at"].timestamp() <= time.time()):
            raise AuthError()
        brain = self.data.get_brain(row["brain_id"])
        if brain is None or brain.id != row["brain_resource_id"]:
            raise AuthError()
        return Principal("access_token", row["id"], row["brain_id"],
                         frozenset(row["permissions"]), token_id=row["id"], legacy=bool(row.get("legacy", False)))

    def authorize_workspace(self, principal, brain_id, permission="memory:read"):
        if principal.is_system:
            return
        if principal.brain_id != brain_id:
            raise AuthError(403, "FORBIDDEN")
        aliases = {"memory:read": "brain:read", "memory:write": "brain:write"}
        if permission not in principal.permissions and aliases.get(permission) not in principal.permissions:
            raise AuthError(403, "FORBIDDEN")


def get_auth_service(data=None):
    if data is None:
        from src.services.data.main import data_adapter
        data = data_adapter
    from src.services.credentials import token_repository
    return AuthService(data, token_repository)


def bearer_token(headers):
    legacy = headers.get("BrainPAT")
    authorization = headers.get("Authorization", "")
    scheme, _, value = authorization.partition(" ")
    bearer = value.strip() if scheme.lower() == "bearer" else None
    if legacy and bearer and not secrets.compare_digest(legacy, bearer):
        raise AuthError()
    return bearer or legacy


def request_permission(method, path):
    # Retrieval POSTs are reads; all ingestion, lifecycle, and graph mutations are writes.
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "memory:read"
    if "/retrieve/" in path or path.endswith(("/search", "/context")):
        return "memory:read"
    return "memory:write"
