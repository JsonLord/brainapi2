import json
import math
from contextlib import contextmanager
from unittest.mock import MagicMock

import httpx
import pytest

from src.adapters.embeddings import EmbeddingsAdapter
from src.config import EmbeddingsConfig, OllamaConfig
from src.lib.embeddings import client_ollama
from src.lib.embeddings.profile import EmbeddingProfileError, index_profile
from src.lib.postgresql import vectors


@pytest.fixture
def gemma(monkeypatch):
    monkeypatch.setenv('OLLAMA_EMBEDDING_MODEL', 'embeddinggemma-2:270m')
    for kind in ('NODES', 'TRIPLETS', 'OBSERVATIONS', 'DATA', 'RELATIONSHIPS'):
        monkeypatch.setenv(f'EMBEDDING_{kind}_DIMENSION', '512')
    for name in ('EMBEDDING_PROFILE', 'EMBEDDING_NATIVE_DIMENSION', 'EMBEDDING_MRL_DIMENSION'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(client_ollama.config, 'embeddings_provider', 'ollama')
    monkeypatch.setattr(client_ollama.config, 'ollama', OllamaConfig())
    monkeypatch.setattr(client_ollama.config, 'embeddings', EmbeddingsConfig(
        'local', provider='ollama', model='embeddinggemma-2:270m'))
    client = client_ollama.OllamaEmbeddingsClient()
    client.client.close()
    yield client
    client.client.close()


def respond(client, raw_vectors, requests=None):
    def handler(request):
        if requests is not None:
            requests.append(json.loads(request.content))
        return httpx.Response(200, content=json.dumps({'embeddings': raw_vectors}))
    client.client = httpx.Client(transport=httpx.MockTransport(handler), base_url='http://127.0.0.1:11434')


def test_documents_and_queries_share_exact_mrl_contract_with_distinct_prefixes(gemma):
    # The discarded tail must have no influence on the norm.
    raw = [3.0, 4.0] + [0.0] * 510 + [999.0] * 256
    requests = []
    respond(gemma, [raw], requests)
    adapter = EmbeddingsAdapter()
    adapter.add_client(gemma)
    document = adapter.embed_text('Orchid launch code: violet-791.').embeddings
    query = adapter.embed_query('What is the Orchid launch code?').embeddings
    assert document == query == [0.6, 0.8] + [0.0] * 510
    assert len(document) == len(query) == 512
    assert math.hypot(*document) == pytest.approx(1)
    assert requests[0]['input'] == ['title: none | text: Orchid launch code: violet-791.']
    assert requests[1]['input'] == ['task: search result | query: What is the Orchid launch code?']
    for request in requests:
        assert request['model'] == 'embeddinggemma-2:270m'
        assert request['truncate'] is False
        assert request['options']['num_ctx'] == 8192
        assert 'dimensions' not in request  # Native output must be transformed by BrainAPI.


def test_mrl_batch_order_and_normalization(gemma):
    respond(gemma, [[1.0] + [0.0] * 767, [0.0, 2.0] + [0.0] * 766])
    results = gemma.embed_texts(['first', 'second'])
    assert [len(v) for v in results] == [512, 512]
    assert results[0][:2] == [1.0, 0.0]
    assert results[1][:2] == [0.0, 1.0]
    assert gemma.embed_texts([]) == []


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -float('inf'), True, '1'])
@pytest.mark.parametrize('index', [0, 767])
def test_invalid_native_values_including_discarded_tail_are_rejected(gemma, invalid, index):
    raw = [1.0] * 768
    raw[index] = invalid
    respond(gemma, [raw])
    with pytest.raises(ValueError, match='invalid embedding'):
        gemma.embed_text('text')


@pytest.mark.parametrize('dimension', [512, 767, 769, 1024])
def test_unexpected_native_dimension_fails_closed(gemma, dimension):
    respond(gemma, [[1.0] * dimension])
    with pytest.raises(ValueError, match='native dimensions'):
        gemma.embed_query('query')


def test_zero_mrl_vector_is_rejected(gemma):
    respond(gemma, [[0.0] * 512 + [1.0] * 256])
    with pytest.raises(ValueError, match='zero'):
        gemma.embed_text('text')


@pytest.mark.parametrize('name,value', [
    ('EMBEDDING_NATIVE_DIMENSION', '512'), ('EMBEDDING_MRL_DIMENSION', '256'),
    ('EMBEDDING_DATA_DIMENSION', '1024'), ('EMBEDDING_PROFILE', 'other-model'),
])
def test_incompatible_gemma_configuration_fails_before_serving(gemma, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match='embeddinggemma2-text-v1 configuration mismatch'):
        EmbeddingsConfig('local', provider='ollama', model='embeddinggemma-2:270m')


@pytest.mark.parametrize('existing_dimension', [512, 1024])
def test_postgres_rejects_incompatible_index_without_dropping_vectors(gemma, monkeypatch, existing_dimension):
    client = vectors.PostgreSQLVectorStoreClient()
    expected = index_profile(client_ollama.config, 512)
    previous = {**expected, 'model': 'qwen3-embedding:0.6b'}
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [(existing_dimension,), (previous,)]
    @contextmanager
    def connect(brain):
        yield connection
    monkeypatch.setattr(client, '_connection', connect)
    monkeypatch.setitem(vectors.EMBEDDING_STORES_SIZES, 'data', 512)
    with pytest.raises(EmbeddingProfileError, match='new empty brain/index'):
        client._ensure_store('data', 'qualification')
    assert all('DROP' not in str(call) for call in cursor.execute.call_args_list)
    assert not client._initialized_stores


def test_fresh_postgres_schema_records_profile_and_512_dimensions(gemma, monkeypatch):
    client = vectors.PostgreSQLVectorStoreClient()
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [None, None]
    @contextmanager
    def connect(brain):
        yield connection
    monkeypatch.setattr(client, '_connection', connect)
    monkeypatch.setattr(vectors, 'EMBEDDING_STORES_SIZES', dict.fromkeys(
        ('nodes', 'triplets', 'observations', 'data', 'relationships'), 512))
    for store in vectors.EMBEDDING_STORES_SIZES:
        cursor.fetchone.side_effect = [None, None]
        client._ensure_store(store, 'qualification')
    executed = cursor.execute.call_args_list
    creates = [str(call.args[0]) for call in executed if 'id BIGINT PRIMARY KEY' in str(call.args[0])]
    assert len(creates) == 5
    assert all('embeddings vector(512)' in ddl for ddl in creates)
    metadata = [json.loads(call.args[1][1]) for call in executed if 'INSERT INTO embedding_profiles' in call.args[0]]
    assert len(metadata) == 5
    assert all(profile['model'] == 'embeddinggemma-2:270m' and profile['stored_dimension'] == 512 for profile in metadata)


def test_profile_mismatch_has_actionable_http_error():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.services.api.errors import install_error_handlers
    app = FastAPI()
    install_error_handlers(app)
    @app.get('/query')
    def query():
        raise EmbeddingProfileError('Existing Qwen index conflicts with configured Gemma profile')
    response = TestClient(app).get('/query')
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'EMBEDDING_PROFILE_MISMATCH'
    assert 'Qwen' in response.json()['detail']
    assert 're-index' in response.json()['error']['resolution']


def test_graph_only_search_does_not_hide_profile_mismatch(monkeypatch):
    import asyncio
    from src.constants.embeddings import Vector
    from src.services.api.constants.requests import SearchRequestBody
    from src.services.api.controllers import search
    monkeypatch.setattr(search.config, 'search_enabled', True)
    embeddings = MagicMock()
    embeddings.embed_query.return_value = Vector(id='q', embeddings=[0.1], metadata={})
    monkeypatch.setattr(search, 'embeddings_adapter', embeddings)
    monkeypatch.setattr(search, '_collect_graph_hits', MagicMock(side_effect=EmbeddingProfileError('incompatible model')))
    with pytest.raises(EmbeddingProfileError, match='incompatible model'):
        asyncio.run(search.search(SearchRequestBody(query='launch code', channels=['entities'])))
