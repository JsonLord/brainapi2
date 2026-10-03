"""
File: /system.py
Created Date: Monday December 1st 2025
Author: Christian Nonis <alch.infoemail@gmail.com>
-----
Last Modified: Monday December 1st 2025 10:13:27 pm
Modified By: Christian Nonis <alch.infoemail@gmail.com>
-----
"""

import asyncio
import re

from src.services.api.constants.requests import CreateBrainRequest
from src.services.brain_lifecycle import BrainLifecycleService
from src.services.data.main import data_adapter


async def get_brains_list():
    """
    Get the list of brains.
    """
    result = await asyncio.to_thread(data_adapter.get_brains_list)
    return result


async def create_new_brain(request: CreateBrainRequest):
    """
    Create a new brain
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", request.brain_id):
        raise ValueError("brain_id contains unsupported characters")
    result = await asyncio.to_thread(data_adapter.create_brain, request.brain_id)
    return result


def get_brain_lifecycle_service() -> BrainLifecycleService:
    # Import lazily so lightweight system-route tests do not initialize every
    # configured storage client before a lifecycle operation is requested.
    from src.core.instances import (
        cache_adapter,
        graph_adapter,
        vector_store_adapter,
    )

    return BrainLifecycleService(
        data_client=data_adapter.data,
        graph_client=graph_adapter.graph,
        vector_client=vector_store_adapter.vector_store,
        cache_client=cache_adapter.cache,
    )


async def clear_brain(brain_id: str, service: BrainLifecycleService):
    result = await asyncio.to_thread(service.clear, brain_id)
    return result.as_dict()


async def delete_brain(brain_id: str, service: BrainLifecycleService):
    result = await asyncio.to_thread(service.delete, brain_id)
    return result.as_dict()
