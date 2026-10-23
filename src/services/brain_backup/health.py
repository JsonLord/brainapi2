"""Bounded, cached readiness probes; only booleans and checkpoint IDs escape."""
import fcntl
import threading
import time
import requests
from .state import public_status

_cache = {}
_guard = threading.Lock()


def checks():
    current = public_status()
    key = current.get('commit')
    with _guard:
        if _cache.get('key') == key and time.monotonic() - _cache.get('time', 0) < 10:
            return dict(_cache['result'])
        result = {'auth': False, 'database': False, 'embedding_profile': False,
                  'access_logging': False, 'remote_reachable': False, 'remote_checkpoint': None}
        from src.services.redaction import access_logging_healthy
        result['access_logging'] = access_logging_healthy()
        try:
            from src.services.auth_config import resolve_system_pat
            result['auth'] = bool(resolve_system_pat())
            from src.services.brain_backup.__main__ import configured
            manager = configured()
            with manager.pg.connect() as conn, conn.cursor() as cur:
                cur.execute('SELECT 1')
                cur.execute('SELECT 1 FROM access_tokens LIMIT 0')
            registry = manager.pg.registry()
            for brain in registry['brains']:
                from src.lib.postgresql._naming import brain_db_name
                with manager.pg.connect(brain_db_name(brain['name_key'])) as conn, conn.cursor() as cur:
                    manager.pg.validate_profile(cur, manager.pg.tables(cur))
            result['database'] = True
            from src.config import config
            from src.lib.embeddings.profile import index_profile
            profile = index_profile(config, 512)
            response = requests.get(f'http://{config.ollama.host}:{config.ollama.port}/api/tags', timeout=3)
            response.raise_for_status()
            models = {m.get('name') for m in response.json().get('models', [])}
            result['embedding_profile'] = (profile['profile'] == 'embeddinggemma2-text-v1'
                and profile['native_dimension'] == 768 and profile['stored_dimension'] == 512
                and profile['model'] in models)
            # Serialize a short remote-head observation with snapshot publication.
            with (manager.root / 'operation.lock').open('a+') as lease:
                try:
                    fcntl.flock(lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    latest = public_status()
                    result.update(remote_reachable=latest.get('remote_verified', False),
                                  remote_checkpoint=latest.get('remote_checkpoint'))
                else:
                    refs = manager.repo.run('ls-remote', '--heads', manager.repo.url,
                        f'refs/heads/{manager.repo.branch}', timeout=5).stdout.decode().splitlines()
                    head = refs[0].split()[0] if refs else None
                    result.update(remote_reachable=True, remote_checkpoint=head)
                    latest = public_status()
                    if head != latest.get('commit'):
                        from .maintenance import fail
                        fail('BACKUP_REMOTE_CONFLICT')
        except Exception:
            # No raw transport/database exception or credential is exposed.
            pass
        _cache.update(key=key, time=time.monotonic(), result=dict(result))
        return result
