from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentSearchRequest(AgentRequest):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)


class AgentContextRequest(AgentRequest):
    query: str = Field(min_length=1)
    max_tokens: int = Field(default=4000, ge=100, le=32000)


class AgentMemoryRequest(AgentRequest):
    text: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
