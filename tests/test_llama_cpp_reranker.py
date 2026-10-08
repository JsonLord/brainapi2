import json

import httpx
import pytest

from src.core.search.hooks import resolve_reranker
from src.lib.reranking import client_llama_cpp


def test_native_reranker_preserves_candidate_identity(monkeypatch):
    monkeypatch.setenv("LLAMA_RERANK_URL", "http://127.0.0.1:11435")
    original_client = httpx.Client

    def handle(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/rerank"
        assert body["documents"] == ["first", "second"]
        assert body["query"] == "query"
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.1},
        ]})

    monkeypatch.setattr(client_llama_cpp.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    fn = resolve_reranker("plugin:llama_cpp")
    result = fn("query", [{"id": "a", "text": "first"}, {"id": "b", "text": "second"}], 2)
    assert [item["id"] for item in result] == ["b", "a"]
    assert result[0]["score"] == 0.9


def test_native_reranker_rejects_out_of_range_result(monkeypatch):
    monkeypatch.setenv("LLAMA_RERANK_URL", "http://127.0.0.1:11435")
    original_client = httpx.Client
    monkeypatch.setattr(client_llama_cpp.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [{"index": 10, "relevance_score": 0.8}]})), **kwargs))
    with pytest.raises(ValueError, match="invalid reranking result"):
        client_llama_cpp.rerank("query", [{"id": "a", "text": "first"}], 1)
