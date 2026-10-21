"""Destructive only to unique disposable test namespaces; run via entrypoint --qualify-registry.

Exercises actual HTTP controllers, PostgreSQL catalogs, Redis, graph/vector clients,
and concurrent registry inserts. Never targets a preexisting brain or external data.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from src.config import config

from src.core.instances import cache_adapter, data_adapter, graph_adapter, vector_store_adapter
from src.lib.postgresql._naming import brain_db_name
from src.lib.postgresql import _provisioning as pg
from src.services.brain_identity import BrainAlreadyExistsError, BrainNotFoundError


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def identities(api):
    response = api.get('/system/brains-list')
    response.raise_for_status()
    return sorted((b['name_key'], b['id']) for b in response.json())


def storage_snapshot():
    with pg.borrow(pg.get_system_pool()) as conn, conn.cursor() as cursor:
        cursor.execute('SELECT id, name_key, document FROM data_brains ORDER BY name_key')
        registry = cursor.fetchall()
        cursor.execute('SELECT document FROM data_workspaces ORDER BY id')
        workspaces = cursor.fetchall()
    with pg.borrow(pg.get_pool(pg._maintenance_dbname())) as conn, conn.cursor() as cursor:
        cursor.execute('SELECT datname FROM pg_database ORDER BY datname')
        databases = cursor.fetchall()
    # Include values, not just key names. Unrelated Celery heartbeat bookkeeping
    # is outside application namespaces and excluded from this comparison.
    redis = cache_adapter.cache.client
    cache = {key: redis.dump(key) for pattern in ('system:brain*', 'codex*')
             for key in redis.scan_iter(match=pattern)}
    return registry, workspaces, databases, cache


def read(api, endpoint, brain_id):
    if endpoint == 'entities':
        return api.get('/retrieve/entities', params={'brain_id': brain_id})
    return api.post('/retrieve/' + endpoint, json={
        'brain_id': brain_id, 'query' if endpoint == 'search' else 'text': 'registry isolation probe',
    })


def qualify():
    suffix = uuid4().hex[:12]
    unknown = 'codex_unknown_' + suffix
    names = ['codex-test-' + suffix, 'codex_test_' + suffix]
    orphan = 'codex_orphan_' + suffix
    race = 'codex_race_' + suffix
    # Cleanup is limited to namespaces proven absent before this test.
    temporary = names + [orphan, race]
    created = set()
    results = {}
    api_url = os.getenv('QUALIFY_API_URL', 'http://127.0.0.1:7860')
    with httpx.Client(base_url=api_url, headers={'BrainPAT': config.system_pat},
                      timeout=120, trust_env=False) as api:
        before_ids = identities(api)
        before = storage_snapshot()
        for name in temporary + [unknown]:
            check(data_adapter.get_brain(name) is None, 'Test namespace collision')
            check(brain_db_name(name) not in {r[0] for r in before[2]}, 'Test storage collision')
        try:
            for endpoint in ('entities', 'search', 'context'):
                response = read(api, endpoint, unknown)
                check(response.status_code == 404, 'Unknown ' + endpoint + ' did not return 404')
                check(response.json()['error']['code'] == 'BRAIN_NOT_FOUND', 'Wrong missing-brain code')
                check(storage_snapshot() == before, 'Unknown read changed registry/workspaces/databases/cache')
                check(identities(api) == before_ids, 'Unknown read changed public identities')
                results['unknown_' + endpoint] = '404 BRAIN_NOT_FOUND; storage/cache unchanged'

            # Defense in depth: bypass HTTP and hit each real storage read path.
            data = data_adapter.data
            graph = graph_adapter.graph._store
            vectors = vector_store_adapter.vector_store
            backend_before = (set(data._initialized_brains), set(graph._schema_ready),
                              dict(graph._brains), set(vectors._initialized_stores),
                              set(vectors._brain_extensions_ready))
            for operation in (
                lambda: data.get_text_chunks(brain_id=unknown),
                lambda: graph.get_brain(unknown),
                lambda: vectors.search_vectors([1.0] + [0.0] * 511, brain_id=unknown, store='data'),
            ):
                try:
                    operation()
                except BrainNotFoundError:
                    pass
                else:
                    raise RuntimeError('Unknown direct backend read accepted')
            check(storage_snapshot() == before, 'Direct backend read changed persistent state')
            check(backend_before == (set(data._initialized_brains), set(graph._schema_ready),
                                    dict(graph._brains), set(vectors._initialized_stores),
                                    set(vectors._brain_extensions_ready)), 'Unknown backend read populated a cache')
            results['direct_backend_reads'] = 'data/graph/vector rejected; catalogs and caches unchanged'

            for name in names:
                created.add(name)
                response = api.post('/system/brains', json={'brain_id': name})
                check(response.status_code == 200, 'Explicit create failed')
                identity = (response.json()['name_key'], response.json()['id'])
                duplicate = api.post('/system/brains', json={'brain_id': name})
                check(duplicate.status_code == 409 and duplicate.json()['error']['code'] == 'BRAIN_ALREADY_EXISTS', 'Duplicate was not conflict')
                check(identity in identities(api) and identities(api) == identities(api), 'Identity changed')
                for endpoint in ('entities', 'search', 'context'):
                    response = read(api, endpoint, name)
                    check(response.status_code == 200, 'Known ' + endpoint + ' failed: ' + str(response.status_code))
                clear = api.post('/system/brains/' + name + '/clear')
                check(clear.status_code == 200, 'Clear rejected supported identifier')
                check(identity in identities(api), 'Clear changed registry identity')
                response = api.delete('/system/brains/' + name)
                check(response.status_code == 200, 'Delete rejected supported identifier')
                check(data_adapter.get_brain(name) is None, 'Delete retained registry')
                check(brain_db_name(name) not in {r[0] for r in storage_snapshot()[2]}, 'Delete retained physical storage')
            results['known_reads_identifiers_duplicates'] = 'GET/search/context 200; stable IDs; duplicate 409; clear/delete hyphen and underscore passed'

            # No preflight lookup: exercise real concurrent storage uniqueness handling.
            created.add(race)
            def create_concurrently(_):
                try:
                    data_adapter.create_brain(race)
                    return 'created'
                except BrainAlreadyExistsError:
                    return 'conflict'
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = sorted(executor.map(create_concurrently, range(2)))
            check(outcomes == ['conflict', 'created'], 'Storage uniqueness race was not a domain conflict')
            results['storage_duplicate_race'] = 'one create, one domain conflict'

            # Create only a physical database, without ever creating a registry row.
            created.add(orphan)
            pg.ensure_database_exists(brain_db_name(orphan))
            check(data_adapter.get_brain(orphan) is None, 'Orphan unexpectedly registered')
            check(all(n != orphan for n, _ in identities(api)), 'Orphan exposed as registered brain')
            def orphan_schema():
                with pg.borrow(pg.get_pool(brain_db_name(orphan))) as conn, conn.cursor() as cur:
                    cur.execute("SELECT schemaname, tablename FROM pg_tables ORDER BY schemaname, tablename")
                    return cur.fetchall()
            orphan_before = orphan_schema()
            for endpoint in ('entities', 'search', 'context'):
                response = read(api, endpoint, orphan)
                check(response.status_code == 404, 'Orphan storage authorized an unknown read')
            check(orphan_schema() == orphan_before, 'Orphan read created tables')
            response = api.delete('/system/brains/' + orphan)
            check(response.status_code == 200 and response.json()['existed'] is False, 'Orphan cleanup failed')
            check(brain_db_name(orphan) not in {r[0] for r in storage_snapshot()[2]}, 'Orphan database remains')
            check(data_adapter.get_brain(orphan) is None, 'Orphan cleanup created a registry row')
            results['orphan_cleanup'] = 'physical database removed without registry creation'
        finally:
            for name in sorted(created):
                response = api.delete('/system/brains/' + name)
                check(response.status_code == 200, 'Temporary brain cleanup failed')
        check(identities(api) == before_ids, 'Temporary qualification changed preexisting brain identities')
    results['result'] = 'passed'
    print(json.dumps(results, sort_keys=True))


if __name__ == '__main__':
    qualify()
