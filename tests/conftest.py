"""Process-wide isolation for the BrainAPI test suite."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

# Installed development plugins must never change core test collection or imports.
# Plugin-specific tests pass their own temporary directories directly to loaders.
_PLUGIN_SANDBOX = tempfile.TemporaryDirectory(prefix="brainapi-test-plugins-")
os.environ["PLUGINS_DIR"] = _PLUGIN_SANDBOX.name

# Configuration is instantiated during module import. Establish deterministic
# non-secret test values before collection so file ordering cannot change it.
_TEST_ENV_DEFAULTS = {
    "BRAINPAT_TOKEN": "test-token",
    "MODELS_MODE": "local",
    "EMBEDDINGS_LOCAL_MODEL": "local-model",
    "EMBEDDINGS_SMALL_MODEL": "small-model",
    "EMBEDDING_NODES_DIMENSION": "3",
    "EMBEDDING_TRIPLETS_DIMENSION": "3",
    "EMBEDDING_OBSERVATIONS_DIMENSION": "3",
    "EMBEDDING_DATA_DIMENSION": "3",
    "EMBEDDING_RELATIONSHIPS_DIMENSION": "3",
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "NEO4J_HOST": "localhost",
    "NEO4J_PORT": "7687",
    "NEO4J_USERNAME": "neo4j",
    "NEO4J_PASSWORD": "test-password",
    "MILVUS_HOST": "localhost",
    "MILVUS_PORT": "19530",
    "MONGO_CONNECTION_STRING": "mongodb://localhost:27017",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "POSTGRES_USERNAME": "postgres",
    "POSTGRES_PASSWORD": "test-password",
    "CELERY_WORKER_CONCURRENCY": "1",
    "OLLAMA_HOST": "localhost",
    "OLLAMA_PORT": "11434",
    "OLLAMA_LLM_SMALL_MODEL": "small",
    "OLLAMA_LLM_LARGE_MODEL": "large",
}
for _key, _value in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)


@pytest.fixture(autouse=True)
def _restore_retrieval_globals_after_test():
    """Undo process-global mocks leaked by overlapping async patch contexts."""

    module = sys.modules.get("src.services.api.controllers.retrieve")
    if module is None:
        yield
        return

    passage_retriever = getattr(module, "_retrieve_passages", None)
    extractor = getattr(module, "_entity_extractor", None)
    extract_elements = getattr(extractor, "extract_elements", None)
    yield
    if passage_retriever is not None:
        module._retrieve_passages = passage_retriever
    if extractor is not None and extract_elements is not None:
        extractor.extract_elements = extract_elements

@pytest.fixture(autouse=True)
def _isolate_explicit_token_registry(request, monkeypatch):
    """Unit tests must opt into credential persistence rather than contacting PG.

    HTTP contract tests inject their own complete repository. Real integration
    gates run inside the production image with PostgreSQL and Redis.
    """
    if request.node.get_closest_marker('integration'):
        return
    from src.services.credentials import token_repository
    monkeypatch.setattr(token_repository, 'find_by_hash', lambda digest: None)
    monkeypatch.setattr(token_repository, 'get', lambda token_id: None)
