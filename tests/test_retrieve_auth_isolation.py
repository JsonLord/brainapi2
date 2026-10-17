import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from src.services.api.app import app

client = TestClient(app)

@pytest.fixture
def mock_data_adapter():
    with patch("src.services.api.middlewares.auth.data_adapter") as mock_da:
        yield mock_da

@pytest.fixture
def mock_cache_adapter():
    with patch("src.services.api.middlewares.auth.cache_adapter") as mock_ca:
        yield mock_ca

def test_workspace_isolation_success(mock_data_adapter, mock_cache_adapter):
    mock_cache_adapter.get.return_value = None

    mock_brain = MagicMock()
    mock_brain.pat = "workspace-token"
    mock_brain.name_key = "test-workspace"
    mock_data_adapter.get_brain.return_value = mock_brain

    # We must patch get_brain_id logic to return the workspace name based on slug if it's there
    # since we are passing header X-Brain-ID, the auth middleware checks it.

    response = client.post(
        "/retrieve/search",
        json={"query": "test"},
        headers={"BrainPAT": "workspace-token", "X-Brain-ID": "test-workspace"}
    )
    # the search endpoint fails because there's no DB, but we get past auth if status != 401/403
    assert response.status_code != 401
    assert response.status_code != 403

def test_workspace_isolation_forbidden(mock_data_adapter, mock_cache_adapter):
    mock_cache_adapter.get.return_value = None

    mock_brain = MagicMock()
    mock_brain.pat = "other-token"
    mock_brain.name_key = "test-workspace"
    mock_data_adapter.get_brain.return_value = mock_brain
    mock_data_adapter.get_brain_by_pat.return_value = None # It means token is unknown to registry

    response = client.post(
        "/retrieve/search",
        json={"query": "test"},
        headers={"BrainPAT": "wrong-token", "X-Brain-ID": "test-workspace"}
    )
    assert response.status_code == 401

def test_workspace_isolation_forbidden_cross_auth(mock_data_adapter, mock_cache_adapter):
    mock_cache_adapter.get.return_value = None

    mock_brain = MagicMock()
    mock_brain.pat = "other-token"
    mock_brain.name_key = "test-workspace"
    mock_data_adapter.get_brain.return_value = mock_brain
    mock_data_adapter.get_brain_by_pat.return_value = MagicMock() # It means token is valid for another brain

    response = client.post(
        "/retrieve/search",
        json={"query": "test"},
        headers={"BrainPAT": "wrong-token", "X-Brain-ID": "test-workspace"}
    )
    # The middleware returns 401 when get_brain_by_pat returns something, because workspace_slug is not set for non-workspace prefixed endpoints!
    assert response.status_code == 401
