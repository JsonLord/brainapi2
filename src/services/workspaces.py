"""Workspace registry built on top of the existing brain registry."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from hashlib import sha1
from urllib.parse import quote, urlsplit

from src.constants.data import Brain, Workspace

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class WorkspaceError(ValueError):
    pass


class WorkspaceConflictError(WorkspaceError):
    pass


class WorkspaceNotFoundError(WorkspaceError):
    pass


class WorkspaceScopeConflictError(WorkspaceError):
    pass


class WorkspaceAccessDeniedError(PermissionError):
    pass


def validate_slug(slug: str) -> str:
    value = (slug or "").strip()
    if not SLUG_RE.fullmatch(value):
        raise WorkspaceError(
            "slug must be 1-63 lowercase ASCII letters, digits, or hyphens, "
            "and must start and end with a letter or digit"
        )
    return value


def slugify(value: str) -> str:
    slug = re.sub(
        r"[^a-z0-9]+", "-", (value or "").strip().lower()
    ).strip("-")
    slug = slug[:63].rstrip("-")
    return slug or f"brain-{sha1(value.encode()).hexdigest()[:8]}"


def display_name_for(brain_id: str) -> str:
    return re.sub(r"[-_]+", " ", brain_id).strip().title() or brain_id


def resolve_workspace_scope(
    workspace: Workspace,
    *,
    header_brain_id: str | None = None,
    query_brain_id: str | None = None,
    body_brain_id: str | None = None,
) -> str:
    """Return the URL workspace brain, rejecting every conflicting explicit scope."""
    explicit = (header_brain_id, query_brain_id, body_brain_id)
    if any(value and value.rstrip() != workspace.brain_id for value in explicit):
        raise WorkspaceScopeConflictError(
            "Workspace URL conflicts with an explicit brain scope."
        )
    return workspace.brain_id


def authorize_brain_pat(
    presented_pat: str,
    *,
    system_pat: str | None,
    brain_pat: str | None,
    workspace_scoped: bool,
) -> None:
    """Authorize the already-resolved brain without changing its scope."""
    if system_pat and secrets.compare_digest(presented_pat, system_pat):
        return
    if brain_pat and secrets.compare_digest(presented_pat, brain_pat):
        return
    if workspace_scoped:
        raise WorkspaceAccessDeniedError("PAT cannot access this workspace")
    raise ValueError("Invalid BrainPAT")


def public_base_url(configured: str | None, request_base_url: str | None = None) -> str:
    value = (configured or request_base_url or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WorkspaceError("PUBLIC_BASE_URL must be an absolute HTTP(S) URL")
    return value


def workspace_payload(workspace: Workspace, base_url: str) -> dict:
    slug = quote(workspace.slug, safe="")
    return {
        **workspace.model_dump(mode="json"),
        "api_base_url": f"{base_url}/brains/{slug}/api",
        "console_url": f"{base_url}/console/w/{slug}/",
    }


class WorkspaceService:
    def __init__(self, repository):
        self.repository = repository

    def list(self) -> list[Workspace]:
        return self.repository.get_workspaces()

    def get(self, slug: str, *, include_archived: bool = False) -> Workspace:
        workspace = self.repository.get_workspace(validate_slug(slug))
        if workspace is None or (workspace.archived and not include_archived):
            raise WorkspaceNotFoundError(f'Workspace "{slug}" was not found')
        return workspace

    def create(
        self, *, slug: str, display_name: str, description: str | None = None
    ) -> Workspace:
        slug = validate_slug(slug)
        display_name = display_name.strip()
        if not display_name:
            raise WorkspaceError("display_name must not be blank")
        if self.repository.get_workspace(slug):
            raise WorkspaceConflictError(f'Workspace slug "{slug}" already exists')
        if self.repository.get_workspace_by_brain_id(slug):
            raise WorkspaceConflictError(f'Brain "{slug}" already has a workspace')
        if self.repository.get_brain(slug):
            raise WorkspaceConflictError(
                f'Brain "{slug}" already exists; run workspace bootstrap'
            )
        self.repository.create_brain(slug)
        return self.repository.create_workspace(
            Workspace(
                brain_id=slug,
                slug=slug,
                display_name=display_name,
                description=description,
            )
        )

    def update(self, slug: str, **changes) -> Workspace:
        workspace = self.get(slug, include_archived=True)
        for field in ("display_name", "description", "archived"):
            if field in changes:
                setattr(workspace, field, changes[field])
        workspace.updated_at = datetime.now(timezone.utc)
        return self.repository.update_workspace(workspace)

    def bootstrap(self) -> list[Workspace]:
        existing = {workspace.brain_id: workspace for workspace in self.list()}
        used_slugs = {workspace.slug for workspace in existing.values()}
        for brain in self.repository.get_brains_list():
            if brain.name_key in existing:
                continue
            slug = slugify(brain.name_key)
            if slug in used_slugs:
                suffix = sha1(brain.name_key.encode()).hexdigest()[:8]
                slug = f"{slug[:54].rstrip('-')}-{suffix}"
            workspace = Workspace(
                brain_id=brain.name_key,
                slug=slug,
                display_name=display_name_for(brain.name_key),
            )
            self.repository.create_workspace(workspace)
            existing[brain.name_key] = workspace
            used_slugs.add(slug)
        return sorted(existing.values(), key=lambda item: item.slug)
