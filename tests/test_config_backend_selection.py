import os
import pytest
from src.config import Config

def test_config_default_backends(monkeypatch):
    monkeypatch.delenv("DATA_DB", raising=False)
    monkeypatch.delenv("VECTOR_DB", raising=False)
    monkeypatch.delenv("GRAPH_DB", raising=False)
    monkeypatch.delenv("MONGO_PORT", raising=False)

    cfg = Config()
    assert cfg.mongo is not None
    assert cfg.milvus is not None
    assert cfg.neo4j is not None
    assert cfg.postgresql is None

def test_config_postgres_profile(monkeypatch):
    monkeypatch.setenv("DATA_DB", "postgresql")
    monkeypatch.setenv("VECTOR_DB", "postgresql")
    monkeypatch.setenv("GRAPH_DB", "networkx")
    monkeypatch.delenv("MONGO_PORT", raising=False)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)

    cfg = Config()
    assert cfg.mongo is None
    assert cfg.milvus is None
    assert cfg.neo4j is None
    assert cfg.postgresql is not None
