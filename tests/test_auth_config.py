import pytest
from src.services.auth_config import resolve_system_pat

@pytest.mark.parametrize('env,expected', [
    ({'SYSTEM_PAT': 'canonical'}, 'canonical'),
    ({'BRAINPAT_TOKEN': 'alias'}, 'alias'),
    ({'SYSTEM_PAT': 'canonical', 'BRAINPAT_TOKEN': 'canonical'}, 'canonical'),
    ({'ENV': 'development'}, None),
])
def test_one_administrator(env, expected):
    assert resolve_system_pat(env) == expected

@pytest.mark.parametrize('env,code', [
    ({'SYSTEM_PAT': 'a', 'BRAINPAT_TOKEN': 'b'}, 'SYSTEM_PAT_CONFLICT'),
    ({}, 'SYSTEM_PAT_REQUIRED'),
    ({'SYSTEM_PAT': 'brainpat_default_token'}, 'SYSTEM_PAT_INVALID'),
])
def test_fail_closed(env, code):
    with pytest.raises(ValueError, match=code):
        resolve_system_pat(env)

def test_legacy_alias_deprecation(caplog):
    from src.services.auth_config import _warn_legacy_alias
    _warn_legacy_alias.cache_clear()
    resolve_system_pat({'BRAINPAT_TOKEN': 'never-log-this'})
    assert 'deprecated' in caplog.text
    assert 'never-log-this' not in caplog.text
