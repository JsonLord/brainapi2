import pytest
from fastapi.testclient import TestClient
from src.services.api.app import app

client = TestClient(app)

def test_openapi_schema_contains_retrieve_endpoints():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    paths = schema.get("paths", {})
    assert "/retrieve/context" in paths
    assert "post" in paths["/retrieve/context"]

    assert "/retrieve/search" in paths
    assert "post" in paths["/retrieve/search"]
    assert "get" in paths["/retrieve/search"]

    # Verify input models are generated correctly in components
    schemas = schema.get("components", {}).get("schemas", {})
    assert "GetContextRequestBody" in schemas
    assert "SearchRequestBody" in schemas

    # Verify GET parameters are present for /retrieve/search
    get_search_params = paths["/retrieve/search"]["get"].get("parameters", [])
    param_names = [p["name"] for p in get_search_params]
    assert "query" in param_names
    assert "k" in param_names
    assert "channels" in param_names
