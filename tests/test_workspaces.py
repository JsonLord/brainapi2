from src.constants.data import Brain, Workspace
from src.services.workspaces import (
    WorkspaceConflictError,
    WorkspaceAccessDeniedError,
    WorkspaceError,
    WorkspaceScopeConflictError,
    WorkspaceService,
    resolve_workspace_scope,
    authorize_brain_pat,
    validate_slug,
    workspace_payload,
    public_base_url,
)


class MemoryRepository:
    def __init__(self, brains=()):
        self.brains = {brain.name_key: brain for brain in brains}
        self.workspaces = {}

    def get_brains_list(self):
        return list(self.brains.values())

    def get_brain(self, name_key):
        return self.brains.get(name_key)

    def create_brain(self, name_key):
        if name_key in self.brains:
            raise ValueError("duplicate brain")
        brain = Brain(name_key=name_key)
        self.brains[name_key] = brain
        return brain

    def get_workspaces(self):
        return list(self.workspaces.values())

    def get_workspace(self, slug):
        return next((item for item in self.workspaces.values() if item.slug == slug), None)

    def get_workspace_by_brain_id(self, brain_id):
        return self.workspaces.get(brain_id)

    def create_workspace(self, workspace):
        if self.get_workspace(workspace.slug) or workspace.brain_id in self.workspaces:
            raise WorkspaceConflictError("duplicate workspace")
        self.workspaces[workspace.brain_id] = workspace
        return workspace

    def update_workspace(self, workspace):
        self.workspaces[workspace.brain_id] = workspace
        return workspace


def test_slug_validation():
    assert validate_slug("aux-research") == "aux-research"
    for invalid in ("Aux", "two words", "-alpha", "alpha-", "a" * 64):
        try:
            validate_slug(invalid)
        except WorkspaceError:
            pass
        else:
            raise AssertionError(f"accepted invalid slug {invalid!r}")


def test_duplicate_slug_and_brain_id_are_rejected():
    repository = MemoryRepository([Brain(name_key="alpha"), Brain(name_key="beta")])
    repository.create_workspace(Workspace(brain_id="alpha", slug="alpha", display_name="Alpha"))
    for duplicate in (
        Workspace(brain_id="beta", slug="alpha", display_name="Same slug"),
        Workspace(brain_id="alpha", slug="other", display_name="Same brain"),
    ):
        try:
            repository.create_workspace(duplicate)
        except WorkspaceConflictError:
            pass
        else:
            raise AssertionError("duplicate workspace was accepted")


def test_bootstrap_existing_brains_is_idempotent():
    repository = MemoryRepository([Brain(name_key="Alpha Brain"), Brain(name_key="beta")])
    service = WorkspaceService(repository)
    first = service.bootstrap()
    second = service.bootstrap()
    assert [(item.brain_id, item.slug) for item in first] == [
        ("Alpha Brain", "alpha-brain"),
        ("beta", "beta"),
    ]
    assert [item.id for item in first] == [item.id for item in second]
    assert len(repository.workspaces) == 2


def test_generated_endpoint_urls_normalize_public_base_url():
    workspace = Workspace(brain_id="alpha", slug="alpha", display_name="Alpha")
    payload = workspace_payload(workspace, "https://host.example")
    assert payload["api_base_url"] == "https://host.example/brains/alpha/api"
    assert payload["console_url"] == "https://host.example/console/w/alpha/"
    assert "pat" not in payload
    assert public_base_url("https://host.example///") == "https://host.example"


def test_workspace_url_is_authoritative():
    alpha = Workspace(brain_id="alpha", slug="alpha", display_name="Alpha")
    assert resolve_workspace_scope(alpha) == "alpha"
    assert resolve_workspace_scope(alpha, header_brain_id="alpha", body_brain_id="alpha") == "alpha"
    try:
        resolve_workspace_scope(alpha, header_brain_id="beta")
    except WorkspaceScopeConflictError:
        pass
    else:
        raise AssertionError("conflicting X-Brain-ID was accepted")


def test_create_workspace_uses_same_brain_registry():
    repository = MemoryRepository()
    workspace = WorkspaceService(repository).create(
        slug="aux-research", display_name="Aux Research", description="Research"
    )
    assert repository.get_brain("aux-research") is not None
    assert workspace.brain_id == "aux-research"


def test_system_and_per_brain_pat_authorization():
    authorize_brain_pat(
        "system-secret", system_pat="system-secret", brain_pat="alpha-secret", workspace_scoped=True
    )
    authorize_brain_pat(
        "alpha-secret", system_pat="system-secret", brain_pat="alpha-secret", workspace_scoped=True
    )
    try:
        authorize_brain_pat(
            "alpha-secret", system_pat="system-secret", brain_pat="beta-secret", workspace_scoped=True
        )
    except WorkspaceAccessDeniedError:
        pass
    else:
        raise AssertionError("alpha PAT accessed beta")


def test_hosted_retrieval_forwards_isolated_brain_scope(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from src.services.api.routes import workspace_api

    facts = {"alpha": "alpha-only fact", "beta": "beta-only fact"}

    async def fake_existing_context_handler(request, brain_id):
        return {"text_context": facts[brain_id]}

    monkeypatch.setattr(workspace_api, "get_context", fake_existing_context_handler)
    alpha = asyncio.run(
        workspace_api.workspace_get_context(SimpleNamespace(), brain_id="alpha")
    )
    beta = asyncio.run(
        workspace_api.workspace_get_context(SimpleNamespace(), brain_id="beta")
    )
    assert "alpha-only" in alpha["text_context"]
    assert "beta-only" not in alpha["text_context"]
    assert "beta-only" in beta["text_context"]
    assert "alpha-only" not in beta["text_context"]
