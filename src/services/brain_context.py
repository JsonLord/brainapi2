from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class BrainContext:
    workspace_slug: str
    brain_id: str
    auth_type: str


brain_context_var: ContextVar[BrainContext | None] = ContextVar(
    "brain_context", default=None
)
