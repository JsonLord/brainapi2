import json
from types import SimpleNamespace
import pytest
from src.services.auth import AuthService, AuthError
from src.services.console_sessions import ConsoleSessions

class Redis:
    def __init__(self): self.values = {}
    def set(self, key, value, ex): self.values[key] = value
    def get(self, key): return self.values.get(key)
    def delete(self, key): self.values.pop(key, None)

@pytest.fixture
def service(monkeypatch):
    monkeypatch.setenv('SYSTEM_PAT', 'session-admin')
    monkeypatch.delenv('BRAINPAT_TOKEN', raising=False)
    return AuthService(SimpleNamespace(get_brain_by_pat=lambda _: None))

def test_session_identity_storage_and_logout(service):
    redis = Redis(); sessions = ConsoleSessions(redis)
    principal = service.authenticate_token('session-admin')
    cookie, csrf, ttl = sessions.create(principal)
    assert 'session-admin' not in json.dumps(redis.values)
    assert cookie not in json.dumps(redis.values)
    assert sessions.resolve(cookie, service)[0].brain_id is None
    assert sessions.resolve(cookie, service)[1] == csrf
    assert ttl == 28800
    sessions.revoke(cookie)
    with pytest.raises(AuthError): sessions.resolve(cookie, service)

def test_system_rotation_invalidates_sessions(service, monkeypatch):
    sessions = ConsoleSessions(Redis())
    cookie, _, _ = sessions.create(service.authenticate_token('session-admin'))
    monkeypatch.setenv('SYSTEM_PAT', 'replacement-admin')
    with pytest.raises(AuthError): sessions.resolve(cookie, service)

def test_invalid_credential_creates_no_session(service):
    with pytest.raises(AuthError): service.authenticate_token('unknown')
