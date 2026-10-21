"""
File: /utils.py
Project: mcp
Created Date: Saturday March 14th 2026
Author: Christian Nonis <alch.infoemail@gmail.com>
-----
Last Modified: Saturday March 14th 2026 3:56:40 pm
Modified By: Christian Nonis <alch.infoemail@gmail.com>
-----
"""

import secrets

from src.config import config
from src.services.brain_identity import BrainNotFoundError, validate_brain_id
from src.services.data.main import data_adapter


def guard_brainpat(pat: str = None, target_brain: str = None) -> bool | str:
    """
    Ensure the brainpat guarantees the access to the requested resource.

    If the BrainPAT is not matching any brain, it will try to match with the systempat,
    if the systempat is not matching, it will raise an auth error.
    """
    from src.services.auth import get_auth_service, AuthError
    service = get_auth_service(data_adapter)
    try:
        principal = service.authenticate_token(pat)
        if target_brain:
            target_brain = validate_brain_id(target_brain)
            service.authorize_workspace(principal, target_brain)
            if data_adapter.get_brain(target_brain) is None:
                raise BrainNotFoundError("Brain not found")
        return True if principal.is_system else principal.brain_id
    except AuthError:
        return False
