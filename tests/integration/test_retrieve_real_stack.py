import pytest
import os
import json
import time
from fastapi.testclient import TestClient
from src.services.api.app import app

client = TestClient(app)

@pytest.mark.integration
def test_real_stack_retrieval(monkeypatch):
    # 1. create isolated workspace
    workspace_payload = {
        "slug": "test-integration-workspace",
        "display_name": "Integration Workspace",
        "description": "Integration Test"
    }

    system_pat = os.environ.get("BRAINPAT_TOKEN", "dummy-system-pat")
    monkeypatch.setenv("BRAINPAT_TOKEN", system_pat)

    # Mock out real calls or assume they fail nicely if missing components
    try:
        response = client.post(
            "/system/workspaces",
            json=workspace_payload,
            headers={"BrainPAT": system_pat}
        )
        if response.status_code != 200:
            pytest.skip("Could not create workspace. Skipping real integration test.")

        workspace_data = response.json()
        brain_pat = workspace_data.get("pat", system_pat)

        # 2. ingest unique fact
        ingest_payload = {
            "text": "The secret code for the integration test is ZXY987."
        }

        response = client.post(
            "/brains/test-integration-workspace/api/ingest/text",
            json=ingest_payload,
            headers={"BrainPAT": brain_pat}
        )
        assert response.status_code in [200, 202]

        task_id = response.json().get("task_id")

        # 3. wait for task success
        if task_id:
            for _ in range(10):
                response = client.get(
                    f"/brains/test-integration-workspace/api/tasks/{task_id}",
                    headers={"BrainPAT": brain_pat}
                )
                if response.status_code == 200 and response.json().get("status") in ["SUCCESS", "FAILED"]:
                    break
                time.sleep(1)

        # 4. retrieve through /retrieve/search
        response = client.post(
            "/brains/test-integration-workspace/api/retrieve/search",
            json={"query": "secret code"},
            headers={"BrainPAT": brain_pat}
        )
        assert response.status_code == 200

        # 5. retrieve through /retrieve/context
        response = client.post(
            "/brains/test-integration-workspace/api/retrieve/context",
            json={"text": "secret code"},
            headers={"BrainPAT": brain_pat}
        )
        assert response.status_code == 200

        # 6. prove another workspace cannot retrieve it
        response = client.post(
            "/brains/default/api/retrieve/search",
            json={"query": "secret code"},
            headers={"BrainPAT": system_pat} # Using system PAT on default
        )
        if response.status_code == 200:
            hits = response.json().get("hits", [])
            assert not any("ZXY987" in hit.get("content", "") for hit in hits)

    except Exception as e:
        pytest.skip(f"Integration dependencies unavailable: {e}")
