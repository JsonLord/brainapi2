"""
File: /tasks.py
Created Date: Saturday December 13th 2025
Author: Christian Nonis <alch.infoemail@gmail.com>
-----
Last Modified: Saturday December 13th 2025
Modified By: the developer formerly known as Christian Nonis at <alch.infoemail@gmail.com>
-----
"""

import json
from src.utils.logging import log
from fastapi import APIRouter, Depends, HTTPException
from src.services.api.dependencies import get_brain_id
from src.services.kg_agent.main import cache_adapter
from src.services.api.constants.responses import TaskListResponse, TaskStateResponse

tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])


def _decode_scoped_task(raw_task: str | bytes, brain_id: str) -> dict | None:
    """Decode a task only when its persisted scope matches the request scope.

    Redis already namespaces task records by brain.  Checking the scope stored
    in newer task payloads as well prevents a corrupt or incorrectly migrated
    record from being disclosed through another workspace.  Older records did
    not include ``brain_id`` and remain readable from their namespaced key.
    """
    if isinstance(raw_task, bytes):
        raw_task = raw_task.decode("utf-8")
    result = json.loads(raw_task)
    if not isinstance(result, dict):
        raise ValueError("Task payload must be an object")
    persisted_brain_id = result.get("brain_id")
    if persisted_brain_id is not None and persisted_brain_id != brain_id:
        return None
    return result


@tasks_router.get("/", response_model=TaskListResponse)
async def get_tasks(brain_id: str = Depends(get_brain_id)):
    try:
        task_keys = cache_adapter.get_task_keys(brain_id)
        results = []
        for task_key in task_keys:
            task_id = task_key.split(":")[-1]
            str_result = cache_adapter.get_task(task_id, brain_id=brain_id)
            if str_result is None:
                continue
            result = _decode_scoped_task(str_result, brain_id)
            if result is None:
                continue
            results.append(
                {
                    **result,
                    "id": task_id,
                    "status": result.get("status", "unknown"),
                }
            )
        return {"tasks": results}
    except Exception as e:
        log(f"Error in get_tasks: {type(e).__name__}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve tasks",
        ) from e


@tasks_router.get("/{task_id}", response_model=TaskStateResponse)
async def get_task(task_id: str, brain_id: str = Depends(get_brain_id)):
    """
    Get the result of a task by its ID.
    """
    try:
        str_result = cache_adapter.get_task(task_id, brain_id=brain_id)
        if str_result is None:
            raise HTTPException(status_code=404, detail="Task not found")
        result = _decode_scoped_task(str_result, brain_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return {
            **result,
            "task_id": task_id,
            "status": result.get("status", "unknown"),
        }
    except HTTPException:
        raise
    except Exception as e:
        log(f"Error in get_task: {type(e).__name__}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve task",
        ) from e
