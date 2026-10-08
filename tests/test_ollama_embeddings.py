import httpx
import pytest

from src.config import OllamaConfig
from src.lib.embeddings import client_ollama


def make_client(monkeypatch, handler):
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    monkeypatch.setenv("OLLAMA_LLM_SMALL_MODEL", "qwen3:0.6b")
    monkeypatch.setattr(client_ollama.config, "ollama", OllamaConfig())
    for kind in ("nodes", "triplets", "observations", "data", "relationships"):
        monkeypatch.setattr(client_ollama.config.embeddings, f"embedding_{kind}_dimension", 2)
    client = client_ollama.OllamaEmbeddingsClient()
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:11434")
    return client


def test_native_embed_uses_dedicated_model_and_preserves_batch_order(monkeypatch):
    import json

    def handle(request):
        body = json.loads(request.content)
        assert request.url.path == "/api/embed"
        assert body["model"] == "qwen3-embedding:0.6b"
        assert body["input"] == ["first", "second"]
        assert body["truncate"] is False
        assert body["options"]["num_thread"] == 2
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    client = make_client(monkeypatch, handle)
    assert client.embed_texts(["first", "second"]) == [[0.1, 0.2], [0.3, 0.4]]


@pytest.mark.parametrize("vectors", [[], [[0.1]], [[0.1], [0.1, 0.2]], [[True], [0.2]], [[], []]])
def test_invalid_embed_response_fails_instead_of_returning_fake_vectors(monkeypatch, vectors):
    client = make_client(monkeypatch, lambda request: httpx.Response(200, json={"embeddings": vectors}))
    with pytest.raises(ValueError):
        client.embed_texts(["first", "second"])


def test_missing_model_is_a_visible_error(monkeypatch):
    client = make_client(monkeypatch, lambda request: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(httpx.HTTPStatusError):
        client.embed_text("hello")


def test_model_dimension_mismatch_is_visible_before_pgvector_write(monkeypatch):
    client = make_client(monkeypatch, lambda request: httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]}))
    with pytest.raises(ValueError, match="reindex"):
        client.embed_text("hello")


def test_legacy_embedding_setting_remains_independent_of_chat(monkeypatch):
    monkeypatch.delenv("OLLAMA_EMBEDDING_MODEL", raising=False)
    monkeypatch.setenv("EMBEDDINGS_LOCAL_MODEL", "legacy-embedding")
    monkeypatch.setenv("OLLAMA_LLM_SMALL_MODEL", "chat")
    config = OllamaConfig()
    assert config.embedding_model == "legacy-embedding"
    assert config.llm_small_model == "chat"
