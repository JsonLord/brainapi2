"""Canonical public brain identifiers and registry domain errors."""

import re


class InvalidBrainIdError(ValueError):
    """Invalid or reserved public brain identifier."""
    code = "BRAIN_ID_INVALID"


class ReservedBrainIdError(InvalidBrainIdError):
    """The identifier belongs to BrainAPI infrastructure."""
    code = "BRAIN_ID_RESERVED"


class BrainNotFoundError(LookupError):
    """The canonical registry does not contain the requested brain."""


class BrainAlreadyExistsError(ValueError):
    """A registry uniqueness constraint rejected brain creation."""


def validate_brain_id(brain_id: str) -> str:
    value = brain_id.strip() if isinstance(brain_id, str) else ""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise InvalidBrainIdError(
            "brain_id must be 1-128 ASCII letters, digits, hyphens, or underscores "
            "and start with a letter or digit"
        )
    if value.lower() == "system":
        raise ReservedBrainIdError('brain_id "system" is reserved')
    return value
