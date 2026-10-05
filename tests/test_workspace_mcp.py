import asyncio

import pytest

from src.services.brain_context import BrainContext, brain_context_var


def test_workspace_mcp_tools_are_registered():
    from src.services.mcp.main import mcp, workspace_mcp

    names = set(workspace_mcp._tool_manager._tools)
    assert {
        "memory_search",
        "memory_context",
        "memory_store",
        "memory_observe",
        "memory_neighbors",
        "memory_entity",
        "memory_task_status",
    } <= names
    assert "search_memory" not in names
    assert "traverse_graph" not in names
    assert "list_brains" not in names
    assert "memory_search" not in set(mcp._tool_manager._tools)


def test_workspace_mcp_semantic_tools_receive_canonical_scope(monkeypatch):
    from src.services.mcp import main

    calls = []

    async def search(query, limit, brain_id):
        calls.append(("search", brain_id))
        return [{"id": brain_id}]

    async def context(query, max_tokens, brain_id):
        calls.append(("context", brain_id))
        return {"context": query, "sources": []}

    async def store(**kwargs):
        calls.append(("store", kwargs["brain_id"]))
        return {"task_id": "alpha-task"}

    async def task(task_id, brain_id):
        calls.append(("task", brain_id))
        if task_id != f"{brain_id}-task":
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Task not found")
        return {
            "task_id": task_id,
            "status": "queued",
            "brain_id": brain_id,
            "workspace": "untrusted-task-payload",
        }

    monkeypatch.setattr(main, "agent_memory_search", search)
    monkeypatch.setattr(main, "agent_memory_context", context)
    monkeypatch.setattr(main, "agent_memory_store", store)
    monkeypatch.setattr(main, "agent_memory_task_status", task)
    token = brain_context_var.set(BrainContext("alpha", "alpha", "brain"))
    try:
        assert asyncio.run(main.memory_search("q"))["results"][0]["id"] == "alpha"
        assert asyncio.run(main.memory_context("q"))["workspace"] == "alpha"
        assert asyncio.run(main.memory_store("fact"))["task_id"] == "alpha-task"
        task_result = asyncio.run(main.memory_task_status("alpha-task"))
        assert task_result["brain_id"] == "alpha"
        assert task_result["workspace"] == "alpha"
    finally:
        brain_context_var.reset(token)
    assert calls == [
        ("search", "alpha"),
        ("context", "alpha"),
        ("store", "alpha"),
        ("task", "alpha"),
    ]

    beta_token = brain_context_var.set(BrainContext("beta", "beta", "brain"))
    try:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            asyncio.run(main.memory_task_status("alpha-task"))
        assert exc.value.status_code == 404
    finally:
        brain_context_var.reset(beta_token)


def test_workspace_mcp_tools_reject_unscoped_direct_use():
    from src.services.mcp.main import memory_search

    with pytest.raises(ValueError, match="workspace MCP endpoint"):
        asyncio.run(memory_search("q"))


def test_workspace_mcp_transport_resolves_path_and_enforces_pat(monkeypatch):
    from src.services.mcp import app as mcp_app
    from tests.test_workspace_facade import FacadeData

    data = FacadeData()
    monkeypatch.setattr(mcp_app, "data_adapter", data)

    async def downstream(scope, _receive, send):
        context = brain_context_var.get()
        body = f"{scope['path']}:{context.brain_id}".encode()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})

    async def invoke(path, pat):
        messages = []
        middleware = mcp_app.AuthContextMiddleware(downstream)
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "headers": [(b"authorization", f"Bearer {pat}".encode())],
            },
            lambda: None,
            messages.append,
        )
        return messages

    monkeypatch.setattr(
        mcp_app,
        "guard_brainpat",
        lambda pat: True
        if pat == "system-secret"
        else ("alpha" if pat == "alpha-secret" else False),
    )
    allowed = asyncio.run(invoke("/brains/alpha/mcp", "alpha-secret"))
    assert allowed[0]["status"] == 200
    assert allowed[1]["body"] == b"/workspace/mcp:alpha"
    forbidden = asyncio.run(invoke("/brains/beta/mcp", "alpha-secret"))
    assert forbidden[0]["status"] == 403
    invalid = asyncio.run(invoke("/brains/alpha/mcp", "invalid"))
    assert invalid[0]["status"] == 401
    archived = asyncio.run(invoke("/brains/archived/mcp", "system-secret"))
    assert archived[0]["status"] == 410
    missing = asyncio.run(invoke("/brains/missing/mcp", "system-secret"))
    assert missing[0]["status"] == 404
