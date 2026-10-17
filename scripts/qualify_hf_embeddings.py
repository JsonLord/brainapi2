"""Run through entrypoint.sh --qualify inside a local pre-production image."""

import json
import math
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from src.config import config
from src.constants.embeddings import EMBEDDING_STORES_SIZES
from src.lib.embeddings.client_ollama import OllamaEmbeddingsClient
from src.lib.embeddings.profile import EmbeddingProfileError
from src.lib.postgresql._provisioning import borrow, get_brain_pool
from src.lib.postgresql.vectors import PostgreSQLVectorStoreClient
from src.services.data.main import data_adapter


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def qualify():
    check(config.ollama.embedding_model == 'embeddinggemma-2:270m', 'Wrong embedding model')
    check(config.pipeline_mode == 'lightweight', 'Qualification requires lightweight ingestion')
    check(config.search_enabled and config.search_use_dense and config.search_use_bm25,
          'Qualification requires dense + BM25 fusion')
    check(set(EMBEDDING_STORES_SIZES.values()) == {512}, 'All stores must use 512 dimensions')
    token = os.environ['BRAINPAT_TOKEN']
    api_url = os.getenv('QUALIFY_API_URL', 'http://127.0.0.1:7860')
    model_url = f'http://{config.ollama.host}:{config.ollama.port}'
    with httpx.Client(base_url=model_url, timeout=120, trust_env=False) as ollama:
        response = ollama.get('/api/tags')
        response.raise_for_status()
        tags = {item['name'] for item in response.json()['models']}
        check(tags == {'embeddinggemma-2:270m'}, f'Unexpected baked models: {sorted(tags)}')
        response = ollama.post('/api/embed', json={
            'model': config.ollama.embedding_model, 'input': 'task: search result | query: launch code',
            'truncate': False, 'options': {'num_ctx': 8192, 'num_thread': 2}})
        response.raise_for_status()
        raw = response.json()['embeddings'][0]
        check(len(raw) == 768 and all(math.isfinite(x) for x in raw), 'Invalid native embedding')
        response = ollama.post('/api/embed', json={
            'model': config.ollama.embedding_model,
            'input': 'task: search result | query: ' + 'hello ' * 9000,
            'truncate': False, 'options': {'num_ctx': 8192, 'num_thread': 2}})
        check(response.status_code == 400 and 'context' in response.json().get('error', '').lower(),
              'Ollama did not enforce the 8192-token input limit')
    client = OllamaEmbeddingsClient()
    try:
        for vector in (client.embed_text('The launch code is violet.'), client.embed_query('launch code')):
            check(len(vector) == 512 and all(math.isfinite(x) for x in vector), 'Invalid MRL vector')
            check(abs(math.hypot(*vector) - 1) < 1e-6, 'MRL vector is not normalized')
    finally:
        client.client.close()
    store = PostgreSQLVectorStoreClient()
    marker = 'violet-' + uuid4().hex
    text = f'The Orchid qualification launch code is {marker}.'
    workspaces = []
    with httpx.Client(base_url=api_url, timeout=120, trust_env=False, follow_redirects=True) as api:
        for suffix in ('a', 'b'):
            slug = 'gemma-qa-' + uuid4().hex[:12] + '-' + suffix
            response = api.post('/system/workspaces', headers={'BrainPAT': token}, json={
                'slug': slug, 'display_name': 'Gemma qualification ' + suffix})
            response.raise_for_status()
            brain_id = response.json()['brain_id']
            # Obtain the scoped PAT locally; never print or persist it in reports.
            pat = data_adapter.get_brain(brain_id).pat
            workspaces.append((slug, brain_id, pat))
        slug, brain_id, pat = workspaces[0]
        root = f'/brains/{slug}/api'
        headers = {'BrainPAT': pat}
        response = api.post(root + '/ingest/', headers=headers, json={
            'data': {'data_type': 'text', 'text_data': text}})
        response.raise_for_status()
        check(response.status_code == 202, 'Ingestion was not accepted')
        task_id = response.json()['task_id']
        deadline = time.monotonic() + 120
        while True:
            response = api.get(root + '/tasks/' + task_id, headers=headers)
            response.raise_for_status()
            state = response.json()
            check(state['status'] != 'failed', 'Worker failed: ' + str(state.get('error')))
            if state['status'] == 'completed':
                break
            check(time.monotonic() < deadline, 'Worker did not complete before deadline')
            time.sleep(1)
        for name in EMBEDDING_STORES_SIZES:
            store._ensure_store(name, brain_id)
        with borrow(get_brain_pool(brain_id)) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT vector_dims(embeddings), metadata FROM vectors_data')
                rows = cursor.fetchall()
                check(rows and all(row[0] == 512 for row in rows), 'Persisted vectors are not 512d')
                for name in EMBEDDING_STORES_SIZES:
                    cursor.execute("SELECT atttypmod FROM pg_attribute WHERE attrelid = %s::regclass AND attname = 'embeddings'", ('vectors_' + name,))
                    check(cursor.fetchone()[0] == 512, 'Incorrect schema dimension: ' + name)
                cursor.execute('SELECT profile FROM embedding_profiles')
                profiles = [row[0] for row in cursor.fetchall()]
                check(len(profiles) == 5 and all(p['model'] == 'embeddinggemma-2:270m' for p in profiles), 'Index profile not recorded')
        requests = [('search', {'query': 'Orchid qualification launch code', 'rerank': 'none'}),
                    ('context', {'text': 'Orchid qualification launch code', 'apply_fact_filter': False})]
        for endpoint, payload in requests:
            response = api.post(root + '/retrieve/' + endpoint, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            if endpoint == 'search':
                check(any(marker in hit['snippet'] for hit in body['hits']), 'Search did not retrieve the unique fact')
            else:
                check(marker in json.dumps(body), 'Context did not include the unique fact')
            other_slug, _, other_pat = workspaces[1]
            other = api.post(f'/brains/{other_slug}/api/retrieve/' + endpoint,
                             headers={'BrainPAT': other_pat}, json=payload)
            other.raise_for_status()
            check(marker not in json.dumps(other.json()), 'Workspace data leaked')
            denied = api.post(f'/brains/{other_slug}/api/retrieve/' + endpoint,
                              headers=headers, json=payload)
            check(denied.status_code == 403, 'Cross-workspace PAT was not denied')
        # Even a same-dimension model identity change must be rejected on a fresh client.
        previous = config.ollama.embedding_model
        try:
            config.ollama.embedding_model = 'qwen3-embedding:0.6b'
            try:
                PostgreSQLVectorStoreClient()._ensure_store('data', brain_id)
            except EmbeddingProfileError:
                pass
            else:
                raise RuntimeError('A same-dimension model identity mismatch was accepted')
        finally:
            config.ollama.embedding_model = previous
    print(json.dumps({'result': 'passed', 'model': config.ollama.embedding_model,
                      'raw_dimension': 768, 'stored_dimension': 512, 'context_limit': 8192,
                      'schema_dimensions': dict(EMBEDDING_STORES_SIZES),
                      'worker': 'completed', 'search': 'unique fact retrieved', 'context': 'unique fact present',
                      'workspace_isolation': 'passed', 'model_mismatch': 'rejected',
                      'qualification_workspaces': [w[0] for w in workspaces]}))


if __name__ == '__main__':
    qualify()
