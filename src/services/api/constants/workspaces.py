from pydantic import BaseModel, Field


class CreateWorkspaceRequest(BaseModel):
    slug: str
    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class UpdateWorkspaceRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    archived: bool | None = None
