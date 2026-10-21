"""Registry authority at the HTTP, PostgreSQL, and MCP boundaries."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.constants.data import Brain
from src.lib.postgresql import _provisioning, data, graph_store, vectors
from src.services.api.errors import install_error_handlers
from src.services.api.middlewares import auth, brains
from src.services.api.routes import retrieve, system
from src.services.api.controllers import system as system_controller
from src.services.brain_identity import (
    BrainAlreadyExistsError, BrainNotFoundError, InvalidBrainIdError, validate_brain_id,
)
from src.services.brain_lifecycle import BrainLifecycleService


VALID_IDS = ['alpha', 'alpha123', 'lifeos-projects', 'probe_readonly_check_123', 'A1-test_name', '123', 'a' * 128]
INVALID_IDS = ['', '-bad', '_bad', 'bad id', 'a/b', 'éclair', 'a' * 129, 'system', 'SYSTEM']


class Registry:
    def __init__(self):
        self.rows = {}
        self.databases = set()
        self.workspaces = {}
        self.cache = {}

    def get_brain(self, name_key):
        return self.rows.get(name_key)

    def create_brain(self, name_key):
        if name_key in self.rows:
            raise BrainAlreadyExistsError('Brain already exists')
        brain = Brain(name_key=name_key)
        self.rows[name_key] = brain
        self.databases.add(name_key)
        return brain

    def get_brains_list(self):
        return sorted(self.rows.values(), key=lambda b: b.name_key)

    def clear_brain_data(self, brain_id):
        pass

    def delete_brain_storage(self, brain_id):
        self.databases.discard(brain_id)

    def delete_brain_registry(self, brain_id):
        return self.rows.pop(brain_id, None) is not None

    def clear_brain_registry_cache(self, brain_id):
        self.cache.pop(brain_id, None)

    def snapshot(self):
        return deepcopy((self.rows, self.databases, self.workspaces, self.cache))


@pytest.fixture
def api(monkeypatch):
    registry = Registry()
    monkeypatch.setenv('BRAINPAT_TOKEN', 'contract-system-pat')
    monkeypatch.setattr(brains, 'data_adapter', registry)
    monkeypatch.setattr(auth, 'data_adapter', registry)
    monkeypatch.setattr(system_controller, 'data_adapter', registry)
    lifecycle = BrainLifecycleService(data_client=registry, graph_client=registry,
                                    vector_client=registry, cache_client=registry)
    monkeypatch.setattr(system, 'get_brain_lifecycle_service', lambda: lifecycle)
    # Controllers are isolated here; real storage + actual retrieval run in integration.
    async def entities(*args, **kwargs):
        return {'message': 'ok', 'entities': [], 'total': 0}
    async def search(*args, **kwargs):
        return {'hits': []}
    async def context(*args, **kwargs):
        return {'text_context': '', 'triples': []}
    monkeypatch.setattr(retrieve, 'retrieve_get_entities_controller', entities)
    monkeypatch.setattr(retrieve, 'search_controller', search)
    monkeypatch.setattr(retrieve, 'retrieve_get_context_controller', context)
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(auth.BrainPATMiddleware)
    app.add_middleware(brains.BrainMiddleware)
    app.include_router(system.system_router)
    app.include_router(retrieve.retrieve_router)
    return TestClient(app), registry


def read(client, kind, brain_id):
    headers = {'BrainPAT': 'contract-system-pat'}
    if kind == 'entities':
        return client.get('/retrieve/entities', params={'brain_id': brain_id}, headers=headers)
    return client.post('/retrieve/' + kind, json={
        'brain_id': brain_id, 'query' if kind == 'search' else 'text': 'probe',
    }, headers=headers)


@pytest.mark.parametrize('kind', ['entities', 'search', 'context'])
@pytest.mark.parametrize('creation_allowed', ['true', 'false'])
def test_unknown_retrieval_is_side_effect_free(api, monkeypatch, kind, creation_allowed):
    client, registry = api
    monkeypatch.setenv('BRAIN_CREATION_ALLOWED', creation_allowed)
    # Deliberately stale positive cache cannot make an orphan authoritative.
    registry.cache['probe_readonly_check_123'] = 'stale-id'
    before = registry.snapshot()
    response = read(client, kind, 'probe_readonly_check_123')
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'BRAIN_NOT_FOUND'
    assert registry.snapshot() == before


@pytest.mark.parametrize('kind', ['entities', 'search', 'context'])
def test_explicitly_provisioned_brain_reads_keep_identity(api, kind):
    client, registry = api
    created = client.post('/system/brains', json={'brain_id': 'known-brain'},
                          headers={'BrainPAT': 'contract-system-pat'})
    assert created.status_code == 200
    before = registry.snapshot()
    response = read(client, kind, 'known-brain')
    assert response.status_code == 200, response.text
    assert registry.snapshot() == before


@pytest.mark.parametrize('brain_id', VALID_IDS)
def test_create_clear_delete_identifier_parity(api, brain_id):
    client, registry = api
    headers = {'BrainPAT': 'contract-system-pat'}
    assert validate_brain_id(brain_id) == brain_id
    assert client.post('/system/brains', headers=headers, json={'brain_id': brain_id}).status_code == 200
    identity = (registry.rows[brain_id].id, registry.rows[brain_id].pat)
    assert client.post(f'/system/brains/{brain_id}/clear', headers=headers).status_code == 200
    assert (registry.rows[brain_id].id, registry.rows[brain_id].pat) == identity
    assert client.delete(f'/system/brains/{brain_id}', headers=headers).status_code == 200
    assert brain_id not in registry.rows and brain_id not in registry.databases


@pytest.mark.parametrize('brain_id', INVALID_IDS)
def test_invalid_identifiers_rejected_consistently(api, brain_id):
    client, registry = api
    with pytest.raises(InvalidBrainIdError) as invalid:
        validate_brain_id(brain_id)
    before = registry.snapshot()
    response = client.post('/system/brains', headers={'BrainPAT': 'contract-system-pat'}, json={'brain_id': brain_id})
    assert response.status_code == 400
    assert response.json()['error']['code'] == invalid.value.code
    assert registry.snapshot() == before
    lifecycle = BrainLifecycleService(data_client=registry, graph_client=registry,
                                    vector_client=registry, cache_client=registry)
    for operation in (lifecycle.clear, lifecycle.delete):
        with pytest.raises(InvalidBrainIdError):
            operation(brain_id)
    assert registry.snapshot() == before


def test_duplicate_create_is_safe_conflict(api):
    client, registry = api
    headers = {'BrainPAT': 'contract-system-pat'}
    assert client.post('/system/brains', headers=headers, json={'brain_id': 'alpha'}).status_code == 200
    before = registry.snapshot()
    response = client.post('/system/brains', headers=headers, json={'brain_id': 'alpha'})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'BRAIN_ALREADY_EXISTS'
    assert registry.rows['alpha'].pat is None
    assert 'pat' not in response.json()
    assert registry.snapshot() == before


def test_orphan_delete_does_not_register_brain(api):
    client, registry = api
    registry.databases.add('orphan_storage')
    response = client.delete('/system/brains/orphan_storage', headers={'BrainPAT': 'contract-system-pat'})
    assert response.status_code == 200
    assert response.json()['existed'] is False
    assert not registry.rows and not registry.databases


def test_postgres_unique_violation_maps_to_domain_conflict(monkeypatch):
    client = data.PostgreSQLDataClient()
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.execute.side_effect = psycopg2.errors.UniqueViolation('secret DB details')
    @contextmanager
    def connect():
        yield connection
    monkeypatch.setattr(client, '_system_connection', connect)
    provision = MagicMock()
    monkeypatch.setattr(client, '_ensure_brain_schema', provision)
    with pytest.raises(BrainAlreadyExistsError, match='^Brain already exists$'):
        client.create_brain('alpha')
    provision.assert_not_called()


def test_registry_list_never_discovers_or_synthesizes_storage_brains(monkeypatch):
    client = data.PostgreSQLDataClient()
    registered = Brain(name_key='persisted')
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        {'document': registered.model_dump(mode='json')},
    ]
    @contextmanager
    def connect():
        yield connection
    monkeypatch.setattr(client, '_system_connection', connect)
    discovery = MagicMock(return_value=['brain_persisted', 'brain_orphan'])
    monkeypatch.setattr(_provisioning, 'list_brain_database_names', discovery)
    assert client.get_brains_list() == client.get_brains_list() == [registered]
    discovery.assert_not_called()


@pytest.mark.parametrize('backend', ['data', 'graph', 'vectors'])
def test_unknown_backend_read_cannot_create_storage_or_cache(monkeypatch, backend):
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    pool = MagicMock()
    pool.getconn.return_value = connection
    monkeypatch.setattr(_provisioning.config, 'data_db', 'postgresql')
    monkeypatch.setattr(_provisioning, 'get_system_pool', lambda: pool)
    allocate = MagicMock(side_effect=AssertionError('brain connection allocated'))
    provision = MagicMock(side_effect=AssertionError('database created'))
    monkeypatch.setattr(_provisioning._registry, 'get', allocate)
    monkeypatch.setattr(_provisioning, 'ensure_database_exists', provision)
    if backend == 'data':
        client = data.PostgreSQLDataClient()
        operation = lambda: client.get_text_chunks(brain_id='unknown')
        snapshots = lambda: deepcopy(client._initialized_brains)
    elif backend == 'graph':
        monkeypatch.setattr(graph_store.config, 'postgresql', SimpleNamespace(validate_credentials=lambda: None))
        client = graph_store.PostgreSQLGraphStore()
        operation = lambda: client.get_brain('unknown')
        snapshots = lambda: deepcopy((client._schema_ready, client._brains))
    else:
        client = vectors.PostgreSQLVectorStoreClient()
        operation = lambda: client.search_vectors([1, 0, 0], brain_id='unknown', store='data')
        snapshots = lambda: deepcopy((client._brain_extensions_ready, client._initialized_stores))
    before = snapshots()
    with pytest.raises(BrainNotFoundError):
        operation()
    assert snapshots() == before
    allocate.assert_not_called()
    provision.assert_not_called()
    statements = [c.args[0] for c in connection.cursor.return_value.__enter__.return_value.execute.call_args_list]
    assert statements and all(s.startswith('SELECT ') for s in statements)


def test_mcp_system_pat_does_not_authorize_unknown_storage(monkeypatch):
    from src.services.mcp import utils
    registry = Registry()
    monkeypatch.setattr(utils, 'data_adapter', registry)
    monkeypatch.setenv('SYSTEM_PAT', 'system-pat')
    monkeypatch.delenv('BRAINPAT_TOKEN', raising=False)
    before = registry.snapshot()
    with pytest.raises(BrainNotFoundError):
        utils.guard_brainpat('system-pat', 'unknown')
    assert registry.snapshot() == before


def test_default_fallback_never_provisions_a_missing_default(api, monkeypatch):
    client, registry = api
    monkeypatch.setenv('DEFAULT_BRAIN_FALLBACK', 'true')
    monkeypatch.setenv('BRAIN_CREATION_ALLOWED', 'true')
    before = registry.snapshot()
    response = client.post('/retrieve/search', json={'query': 'probe'}, headers={'BrainPAT': 'contract-system-pat'})
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'BRAIN_NOT_FOUND'
    assert registry.snapshot() == before


def test_stale_graph_and_vector_cache_cannot_replace_registry(monkeypatch):
    def missing(brain_id):
        raise BrainNotFoundError('Brain not found')
    monkeypatch.setattr(graph_store.config, 'postgresql', SimpleNamespace(validate_credentials=lambda: None))
    monkeypatch.setattr(graph_store, 'require_registered_brain', missing)
    graph = graph_store.PostgreSQLGraphStore()
    graph._brains['deleted'] = graph_store._BrainGraph('deleted')
    graph._schema_ready.add('deleted')
    with pytest.raises(BrainNotFoundError):
        graph.get_brain('deleted')
    monkeypatch.setattr(vectors, 'require_registered_brain', missing)
    vector = vectors.PostgreSQLVectorStoreClient()
    vector._brain_extensions_ready.add('deleted')
    with pytest.raises(BrainNotFoundError):
        vector._ensure_extension('deleted')


def test_mongo_unique_violation_maps_to_domain_conflict(monkeypatch):
    from pymongo.errors import DuplicateKeyError
    from src.config import config
    monkeypatch.setattr(config, 'mongo', SimpleNamespace(
        system_database='system', connection_string='mongodb://localhost:27017'))
    from src.lib.mongo.client import MongoClient
    client = MongoClient.__new__(MongoClient)
    collection = MagicMock()
    collection.insert_one.side_effect = DuplicateKeyError('secret database details')
    client.get_collection = MagicMock(return_value=collection)
    with pytest.raises(BrainAlreadyExistsError, match='^Brain already exists$'):
        client.create_brain('alpha')
    collection.create_index.assert_called_once_with('name_key', unique=True)


def test_mixed_profile_provisions_postgres_only_on_explicit_create(monkeypatch):
    from src.adapters.data import DataAdapter
    from src.config import config
    from src.services.data import main
    monkeypatch.setattr(config, 'data_db', 'mongo')
    monkeypatch.setattr(config, 'vector_db', 'postgresql')
    registry = Registry()
    monkeypatch.setattr(main, 'data_adapter', registry)
    adapter = DataAdapter()
    adapter.add_client(registry)
    allocate = MagicMock()
    monkeypatch.setattr(_provisioning, 'ensure_brain_database', allocate)
    with pytest.raises(BrainNotFoundError):
        _provisioning.require_registered_brain('unknown')
    allocate.assert_not_called()
    brain = adapter.create_brain('mixed-profile')
    allocate.assert_called_once_with(brain.name_key)
    _provisioning.require_registered_brain('mixed-profile')


def test_demo_seed_explicitly_provisions_a_missing_brain(monkeypatch):
    from scripts import seed_public_demo
    from unittest.mock import Mock
    monkeypatch.setenv('BRAINPAT_TOKEN', 'seed-system-pat')
    monkeypatch.setattr('sys.argv', ['seed_public_demo.py', '--brain', 'seed-demo'])
    def response(status, payload=None, text=''):
        result = Mock(status_code=status, text=text)
        result.json.return_value = payload
        return result
    get = Mock(side_effect=[response(200, text='docs'),
                            response(404, {'error': {'code': 'BRAIN_NOT_FOUND'}}),
                            response(200, {'status': 'completed'})])
    post = Mock(side_effect=[response(200, {'name_key': 'seed-demo'}),
                            response(202, {'task_id': 'seed-task'})])
    monkeypatch.setattr(seed_public_demo.requests, 'get', get)
    monkeypatch.setattr(seed_public_demo.requests, 'post', post)
    assert seed_public_demo.main() == 0
    assert post.call_args_list[0].args[0].endswith('/system/brains')
    assert post.call_args_list[0].kwargs['json'] == {'brain_id': 'seed-demo'}
    assert post.call_args_list[1].args[0].endswith('/ingest/')
