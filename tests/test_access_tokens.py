from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from src.constants.data import Brain
from src.services.auth import AuthError, AuthService, token_digest
from src.services.credentials import public_metadata
from src.services.console_sessions import ConsoleSessions
from tests.test_console_sessions import Redis

@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setenv('SYSTEM_PAT', 'registry-admin')
    monkeypatch.delenv('BRAINPAT_TOKEN', raising=False)
    brain = Brain(name_key='alpha')
    data = Mock(get_brain=lambda name: brain if name == 'alpha' else None)
    data.get_brain_by_pat.return_value = None
    row = dict(id='credential-id', brain_id='alpha', brain_resource_id=brain.id,
               token_hash=token_digest('bpat_test-random-value'), permissions=['memory:read'],
               expires_at=None, revoked_at=None, label='integration', token_prefix='bpat_test')
    store = Mock()
    store.find_by_hash.side_effect = lambda digest: row if digest == row['token_hash'] else None
    store.get.return_value = row
    return AuthService(data, store), row, brain

def test_resources_have_no_automatic_credentials():
    brain = Brain(name_key='new-resource')
    assert brain.pat is None
    assert 'pat' not in brain.model_dump()
    legacy = Brain(name_key='old-resource', pat='legacy-value')
    assert 'legacy-value' not in legacy.model_dump_json()
    assert 'legacy-value' not in repr(legacy)

def test_scope_and_permissions(identity):
    service, row, brain = identity
    principal = service.authenticate_token('bpat_test-random-value')
    service.authorize_workspace(principal, 'alpha')
    for scope, permission in [('beta', 'memory:read'), ('alpha', 'memory:write')]:
        with pytest.raises(AuthError) as error: service.authorize_workspace(principal, scope, permission)
        assert error.value.status == 403
    service.token_store.touch.assert_called_once_with(row['id'])
    assert 'token_hash' not in public_metadata(row)

@pytest.mark.parametrize('state', ['unknown', 'revoked', 'expired', 'recreated'])
def test_invalid_token_states(identity, state):
    service, row, brain = identity
    token = 'bpat_test-random-value'
    if state == 'unknown': token = 'bpat_unknown'
    if state == 'revoked': row['revoked_at'] = datetime.now(timezone.utc)
    if state == 'expired': row['expires_at'] = datetime.now(timezone.utc) - timedelta(seconds=1)
    if state == 'recreated': row['brain_resource_id'] = 'previous-resource'
    with pytest.raises(AuthError): service.authenticate_token(token)
    service.token_store.touch.assert_not_called()

def test_revocation_invalidates_derived_console_session(identity):
    service, row, brain = identity
    sessions = ConsoleSessions(Redis())
    cookie, _, _ = sessions.create(service.authenticate_token('bpat_test-random-value'))
    assert sessions.resolve(cookie, service)[0].brain_id == 'alpha'
    row['revoked_at'] = datetime.now(timezone.utc)
    with pytest.raises(AuthError): sessions.resolve(cookie, service)

def test_legacy_credential_scope(identity):
    service, row, brain = identity
    brain.pat = 'legacy-value'
    service.data.get_brain_by_pat.return_value = brain
    principal = service.authenticate_token('legacy-value')
    assert principal.legacy and not principal.is_system
    service.authorize_workspace(principal, 'alpha', 'memory:write')
    with pytest.raises(AuthError): service.authorize_workspace(principal, 'beta')
