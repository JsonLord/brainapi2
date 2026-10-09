import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from src.services.api.app import app

client = TestClient(app)

def test_retrieve_search_post_unauthorized():
    response = client.post("/retrieve/search", json={"query": "test"})
    assert response.status_code == 401

def test_retrieve_context_post_unauthorized():
    response = client.post("/retrieve/context", json={"text": "test"})
    assert response.status_code == 401

@patch("src.services.api.routes.retrieve.search_controller")
def test_retrieve_search_post_authorized(mock_search_controller, monkeypatch):
    mock_search_controller.return_value = {"hits": [], "node_ids": []}
    monkeypatch.setenv("BRAINPAT_TOKEN", "test-token")

    response = client.post(
        "/retrieve/search",
        json={"query": "test", "k": 5},
        headers={"BrainPAT": "test-token"}
    )
    assert response.status_code == 200

    args, _ = mock_search_controller.call_args
    body = args[0]
    assert body.query == "test"
    assert body.k == 5
    assert body.channels == ["passages"]

@patch("src.services.api.routes.retrieve.retrieve_get_context_controller")
def test_retrieve_context_post_authorized(mock_context_controller, monkeypatch):
    mock_context_controller.return_value = {
        "text_context": "Found 3 passages and 10 triples related to the query...",
        "triples": [],
        "historical_context": [],
        "source_passages": [],
        "graph_session_ids": [],
        "temporal_conflicts": [],
        "paths": [],
        "topics": [],
        "stage_timings": {"retrieval": 0.15, "formatting": 0.02}
    }
    monkeypatch.setenv("BRAINPAT_TOKEN", "test-token")

    response = client.post(
        "/retrieve/context",
        json={"text": "Identify memory leaks in Python"},
        headers={"BrainPAT": "test-token"}
    )
    assert response.status_code == 200

    args, _ = mock_context_controller.call_args
    body = args[0]
    assert body.text == "Identify memory leaks in Python"
    assert body.max_facts == 40

@patch("src.services.api.routes.retrieve.search_controller")
def test_retrieve_search_get_authorized(mock_search_controller, monkeypatch):
    mock_search_controller.return_value = {"hits": [], "node_ids": []}
    monkeypatch.setenv("BRAINPAT_TOKEN", "test-token")

    response = client.get(
        "/retrieve/search?query=test&k=5",
        headers={"BrainPAT": "test-token"}
    )
    assert response.status_code == 200

    args, _ = mock_search_controller.call_args
    body = args[0]
    assert body.query == "test"
    assert body.k == 5
    assert body.channels == ["passages"]

@patch("src.services.api.routes.retrieve.search_controller")
def test_retrieve_search_validation_error(mock_search_controller, monkeypatch):
    monkeypatch.setenv("BRAINPAT_TOKEN", "test-token")

    # Missing required query param
    response = client.post(
        "/retrieve/search",
        json={"k": 5},
        headers={"BrainPAT": "test-token"}
    )
    assert response.status_code == 422

    # Range validation k > 200
    response = client.post(
        "/retrieve/search",
        json={"query": "test", "k": 201},
        headers={"BrainPAT": "test-token"}
    )
    assert response.status_code == 422

@patch("src.services.api.routes.retrieve.search_controller")
def test_retrieve_search_channels_validation(mock_search_controller, monkeypatch):
    monkeypatch.setenv("BRAINPAT_TOKEN", "test-token")
    mock_search_controller.return_value = {"hits": [], "node_ids": []}

    response = client.get(
        "/retrieve/search?query=test&channels=passages,entities",
        headers={"BrainPAT": "test-token"}
    )
    assert response.status_code == 200
    args, _ = mock_search_controller.call_args
    body = args[0]
    assert body.channels == ["passages", "entities"]

def test_retrieve_search_plugin_reranker_error(monkeypatch):
    monkeypatch.setenv("BRAINPAT_TOKEN", "test-token")

    response = client.post(
        "/retrieve/search",
        json={"query": "test", "rerank": "plugin:unknown_plugin"},
        headers={"BrainPAT": "test-token"}
    )
    # the endpoint returns 404 for plugin not found during execution of search_controller
    assert response.status_code in [400, 404]
