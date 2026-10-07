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
    brainpat = request.headers.get("BrainPAT")
    if not brainpat:
        auth = request.headers.get("Authorization")
        if auth:
            brainpat = auth.split(" ")[-1].rstrip()
    if not brainpat:
        raise HTTPException(status_code=401, detail="Invalid or missing BrainPAT header")

    system_pat = os.getenv("BRAINPAT_TOKEN", "brainpat_default_token")
    if brainpat == system_pat:
        return {"is_system_pat": True, "brain_id": "default"}

    try:
        brain = await asyncio.to_thread(data_adapter.get_brain_by_pat, brainpat)
    except Exception:
        brain = None

    if not brain:
        if brainpat == system_pat:
            return {"is_system_pat": True, "brain_id": "default"}
        return {"is_system_pat": False, "brain_id": "default"}

    return {"is_system_pat": False, "brain_id": brain.name_key}


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
