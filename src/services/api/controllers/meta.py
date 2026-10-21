"""
File: /meta.py
Created Date: Friday November 7th 2025
Author: Christian Nonis <alch.infoemail@gmail.com>
-----
Last Modified: Saturday December 27th 2025
Modified By: the developer formerly known as Christian Nonis at <alch.infoemail@gmail.com>
-----
"""

import asyncio
import os

from fastapi import HTTPException, Request

from src.services.data.main import data_adapter
from src.services.kg_agent.main import graph_adapter


async def get_login_info(request: Request):
    from src.services.auth import get_auth_service, bearer_token, AuthError
    try:
        principal = get_auth_service(data_adapter).authenticate_token(bearer_token(request.headers))
    except AuthError as error:
        raise HTTPException(status_code=error.status, detail=error.code) from None
    return {"is_system_pat": principal.is_system, "brain_id": principal.brain_id}


async def get_entities_labels(brain_id: str):
    """
    Retrieve node labels (types) for the graph identified by `brain_id`.
    """
    try:
        result = await asyncio.to_thread(graph_adapter.get_graph_node_types, brain_id)
        return result or []
    except Exception:
        return []

async def get_relationships_properties(brain_id: str):
    """
    Retrieve relationship types for the graph associated with the given brain.
    """
    try:
        result = await asyncio.to_thread(graph_adapter.get_graph_relationship_types, brain_id)
        return result or []
    except Exception:
        return []

async def get_entity_properties(brain_id: str):
    """
    Retrieve all unique property keys for node entities in the specified graph.
    """
    try:
        result = await asyncio.to_thread(graph_adapter.get_graph_node_properties, brain_id)
        return result or []
    except Exception:
        return []
