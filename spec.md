# BrainAPI2 Multi-Brain Workspaces Specification

**Repository:** `JsonLord/brainapi2`  
**Target:** extend the existing BrainAPI2 multi-`brain_id` foundation into first-class, independently addressable **brain workspaces** with a tabbed Console and a stable hosted API URL for each workspace.

---

## 1. Objective

Transform BrainAPI2 from a system where multiple brains technically exist but are primarily selected as session context into a system where each brain is presented and managed as a first-class **workspace**.

A workspace must:

1. map 1:1 to an existing BrainAPI `brain_id` / `name_key`;
2. have display metadata and a stable slug;
3. appear as a switchable tab in the Console;
4. retain the current page when switching where possible;
5. have its own stable, shareable API base URL on the same deployment;
6. isolate all graph, observations, vector, task, ingestion, and retrieval operations to that workspace;
7. continue to support the existing `X-Brain-ID` API behavior for backward compatibility;
8. work on a single-host deployment, including Hugging Face Spaces or another reverse-proxied host, without requiring one process/container per brain.

The desired mental model is:

```text
One BrainAPI deployment
│
├── Workspace: company
│   ├── Console: /console/w/company/...
│   └── API:     /brains/company/api/...
│
├── Workspace: aux-research
│   ├── Console: /console/w/aux-research/...
│   └── API:     /brains/aux-research/api/...
│
└── Workspace: agent-memory
    ├── Console: /console/w/agent-memory/...
    └── API:     /brains/agent-memory/api/...
```

---

## 2. Existing repo behavior to preserve

The implementation should build on the existing architecture rather than replace it.

Observed current behavior in `main`:

- The Console is a React application under `console/src`.
- `console/src/App.tsx` defines routes for Overview, Graph, Data, Observations, Vectors, Tasks, and Ingest.
- `console/src/lib/api.ts` already:
  - stores the active `brainId` in the session;
  - calls `/system/brains-list` for system-PAT sessions;
  - sends `X-Brain-ID` for non-system API requests;
  - supports an `overrideBrainId` argument;
  - distinguishes system PAT from per-brain PAT.
- `console/src/components/Layout.tsx` already contains a `BrainSwitcher` based on a `<Select>` and changes the session brain followed by `window.location.reload()`.
- Existing ingestion/retrieval APIs already understand `brain_id` / brain scoping.

Therefore this project is primarily a **workspace registry + routing + Console state/UX + path-scoped API facade** project. Do not fork or duplicate the core graph/retrieval engine for each workspace.

---

## 3. Terminology

### Brain
The existing BrainAPI data isolation primitive identified by `brain_id` / `name_key`.

### Workspace
The user-facing and API-facing representation of one brain. It adds metadata, routing, discoverability, and a stable endpoint while using the existing brain as its storage/execution scope.

### Workspace slug
A URL-safe, immutable or carefully-renamable identifier used in Console and hosted API routes.

Example: `aux-research`.

### Active workspace
The workspace currently rendered by a Console tab/session. It MUST NOT be a global server-side singleton.

---

## 4. Core design decision

### 4.1 Keep one BrainAPI process

Do **not** launch one FastAPI process, database, worker, or port per workspace.

All workspaces share the deployment and infrastructure. Existing `brain_id` filtering remains the isolation mechanism.

### 4.2 Add path-scoped workspace URLs

Each workspace gets a stable public base URL:

```text
{PUBLIC_BASE_URL}/brains/{workspace_slug}/api
```

Examples:

```text
https://brain.example.com/brains/company/api
https://brain.example.com/brains/aux-research/api
https://brain.example.com/brains/agent-memory/api
```

Calls below that base are routed to the existing BrainAPI handlers with the corresponding brain/workspace scope injected server-side.

Example:

```http
POST /brains/aux-research/api/retrieve/context
```

must behave like the existing:

```http
POST /retrieve/context
X-Brain-ID: aux-research
```

The path-scoped form is the canonical hosted endpoint for integrations. The header-scoped legacy API remains supported.

### 4.3 Never trust a conflicting brain header on a workspace URL

For a request to:

```text
/brains/alpha/api/...
```

`alpha` is authoritative.

If the caller also supplies `X-Brain-ID: beta`, reject with `400` or `409`. Never silently route to `beta`.

---

## 5. Workspace data model

Introduce a small persistent workspace registry. Reuse an existing metadata/database abstraction if the repo already has a suitable one; do not add a new database technology solely for this feature.

Minimum model:

```python
Workspace {
    id: UUID | existing stable identifier
    brain_id: str             # existing BrainAPI scope; unique
    slug: str                 # URL-safe; unique
    display_name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    archived: bool = False
}
```

Optional metadata, only if it fits current patterns cleanly:

```python
icon: str | None
tags: list[str]
metadata: dict
```

Do not store secrets in workspace metadata.

### 5.1 Constraints

- `brain_id` unique.
- `slug` unique and normalized.
- slug syntax: lowercase ASCII letters, digits, and `-` only.
- reject reserved slugs such as `system`, `api`, `console`, `docs`, `openapi.json`, `health`, `default` only if they create an actual routing collision.
- default maximum slug length: 63 unless existing project conventions dictate otherwise.

### 5.2 Default migration

Existing installations must continue working.

On startup or through an idempotent migration/bootstrap:

- enumerate existing brains using the canonical current source of truth;
- ensure each has a workspace record;
- preserve the existing `name_key` as `brain_id`;
- generate a stable slug from it;
- make `default` available as a workspace if the default brain exists.

This migration MUST be idempotent.

---

## 6. Workspace management API

Prefer the repo's existing FastAPI/router conventions and auth dependencies.

All workspace-management endpoints require a **system-level PAT** unless the current authorization model already provides a stronger equivalent.

### 6.1 List workspaces

```http
GET /system/workspaces
```

Response:

```json
{
  "workspaces": [
    {
      "id": "...",
      "brain_id": "aux-research",
      "slug": "aux-research",
      "display_name": "Aux Research",
      "description": null,
      "archived": false,
      "api_base_url": "https://host.example/brains/aux-research/api",
      "console_url": "https://host.example/console/w/aux-research/"
    }
  ]
}
```

### 6.2 Get workspace

```http
GET /system/workspaces/{slug}
```

### 6.3 Create workspace

```http
POST /system/workspaces
Content-Type: application/json

{
  "slug": "aux-research",
  "display_name": "Aux Research",
  "description": "Customer and ethnographic research memory"
}
```

Creation must create or register the underlying BrainAPI brain using the existing canonical brain creation mechanism. Do not create a second parallel concept of a brain.

If the repository already has a create-brain endpoint/service, call that service rather than duplicating its logic.

### 6.4 Update workspace metadata

```http
PATCH /system/workspaces/{slug}
```

Initially support:

- `display_name`
- `description`
- `archived`

Treat slug changes as a separate operation or leave them out of v1 to keep integration URLs stable.

### 6.5 Delete/archive

Prefer archive over destructive graph deletion.

```http
DELETE /system/workspaces/{slug}
```

should either:

- archive the workspace only; or
- require an explicit destructive mode already supported by BrainAPI.

Do not accidentally delete graph data simply because a UI tab is removed.

### 6.6 Endpoint discovery

```http
GET /system/workspaces/{slug}/endpoint
```

Response:

```json
{
  "workspace": "aux-research",
  "brain_id": "aux-research",
  "api_base_url": "https://host.example/brains/aux-research/api",
  "console_url": "https://host.example/console/w/aux-research/",
  "auth": {
    "accepted": ["Authorization: Bearer <PAT>", "BrainPAT: <PAT>"]
  }
}
```

Do not return PAT values.

---

## 7. Hosted per-workspace API facade

Add a routing layer under:

```text
/brains/{workspace_slug}/api
```

### 7.1 Required behavior

The facade must make the existing public BrainAPI endpoints usable with no explicit `brain_id` selection from the caller.

At minimum cover the currently public/workhorse endpoint families:

```text
/ingest/*
/retrieve/*
/meta/*                 # only brain-scoped meta calls; system endpoints stay system-scoped
```

and any other currently supported brain-scoped routes discovered during implementation.

### 7.2 Preferred implementation

Do not implement an HTTP loopback proxy from BrainAPI back into itself.

Preferred order:

1. refactor route logic into reusable services/dependencies if needed;
2. mount/reuse routers with a workspace-scope dependency; or
3. add middleware/route dependency that resolves `{workspace_slug}` to `brain_id` and provides the same request context consumed by current handlers.

Avoid network proxying to `localhost` because it adds latency, streaming complexity, duplicated auth, and failure modes.

### 7.3 Scope resolution

Introduce one canonical resolver, conceptually:

```python
resolve_brain_scope(
    authenticated_identity,
    workspace_slug=None,
    x_brain_id=None,
    body_brain_id=None,
) -> brain_id
```

Rules:

1. workspace route slug wins as the requested scope;
2. conflicting header/body scope is rejected;
3. per-brain PAT may only access its allowed brain;
4. system PAT may access any non-archived workspace;
5. legacy unscoped routes retain current behavior.

Every handler should ultimately use this central resolver rather than inventing its own precedence rules.

### 7.4 Public URL construction

Add or reuse a setting:

```text
PUBLIC_BASE_URL=https://brain.example.com
```

If unset, derive from the incoming request only where safe and consistent with existing proxy/header behavior.

Honor trusted proxy headers only according to existing server/deployment policy; do not blindly trust arbitrary `X-Forwarded-*` headers.

Strip trailing slash during normalization.

### 7.5 OpenAPI

Keep the global OpenAPI document working.

Additionally consider:

```http
GET /brains/{workspace_slug}/api/openapi.json
```

If implemented, it should describe the workspace API with server/base URL already scoped to that workspace. This is highly useful for agents and generated clients.

This is desirable for v1 but may be implemented after the essential facade routes if OpenAPI mounting creates disproportionate complexity.

---

## 8. Console information architecture

### 8.1 Route workspaces explicitly

Replace implicit session-only workspace context with URL-addressable workspace routing.

Target routes:

```text
/console/w/:workspaceSlug/
/console/w/:workspaceSlug/graph
/console/w/:workspaceSlug/data
/console/w/:workspaceSlug/observations
/console/w/:workspaceSlug/vectors
/console/w/:workspaceSlug/tasks
/console/w/:workspaceSlug/ingest
/console/w/:workspaceSlug/settings
```

Keep `/console/` backward compatible by redirecting to:

1. last active accessible workspace, else
2. `default`, else
3. first accessible workspace, else
4. an empty-state workspace creation screen for a system PAT.

### 8.2 Workspace tabs

Replace the current primary `<Select>` switcher on desktop with a top-level tab strip.

Desired behavior:

```text
[ Company ] [ Aux Research ] [ Agent Memory ] [ + ]
```

Requirements:

- active tab is visually obvious;
- click switches workspace without a full browser reload;
- switching preserves the current subpage when valid:
  - `/w/company/graph` → `/w/aux-research/graph`;
- each tab uses `display_name`, with slug available as tooltip/subtext where useful;
- horizontal overflow scrolls gracefully;
- keyboard accessible;
- mobile may use the existing/select-style compact switcher if tabs do not fit;
- `+` opens workspace creation only for authorized system-level credentials;
- archived workspaces are excluded by default.

The existing selector can remain as a mobile/fallback component.

### 8.3 No `window.location.reload()`

Workspace switching must update React state/router context and refetch the active page.

Remove the existing reload-based switch path.

### 8.4 Workspace context provider

Create a central client-side provider/hook, for example:

```ts
<WorkspaceProvider>
useWorkspace()
```

It should expose roughly:

```ts
{
  workspaces,
  activeWorkspace,
  activeBrainId,
  loading,
  error,
  canManageWorkspaces,
  switchWorkspace(slug),
  refreshWorkspaces(),
}
```

The route parameter is the primary active-workspace source of truth.

Avoid duplicating workspace state independently across pages.

### 8.5 API client behavior

Update `console/src/lib/api.ts` so page calls derive brain context from the active workspace rather than depending only on a mutable global session brain.

Preferred pattern:

- authentication session stores server URL + credentials;
- workspace routing stores active workspace;
- API calls accept/derive workspace explicitly;
- legacy `session.brainId` may be retained temporarily for migration/backward compatibility, but should no longer be the sole source of truth.

A tab change must not require rewriting authentication credentials.

---

## 9. Workspace settings page

Add:

```text
/console/w/:workspaceSlug/settings
```

Show:

- display name;
- slug;
- brain ID;
- description;
- status;
- canonical API base URL;
- canonical Console URL;
- copy buttons;
- example cURL request using that workspace URL;
- archive action if authorized.

Never display stored PATs.

Example integration snippet:

```bash
curl -X POST \
  "$WORKSPACE_API_URL/retrieve/context" \
  -H "Authorization: Bearer $BRAINPAT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"What do we know about this customer?"}'
```

Notice that no `brain_id` or `X-Brain-ID` is needed when using the workspace API URL.

---

## 10. Workspace creation UI

For system credentials add a create-workspace dialog/page.

Fields:

- name (required);
- slug (auto-generated, editable before creation);
- description (optional).

On success:

1. refresh workspace list;
2. create a new tab;
3. navigate to `/console/w/{slug}/`;
4. show the workspace API URL prominently.

Handle duplicate slug/brain errors inline.

---

## 11. Isolation requirements

This section is non-negotiable.

### 11.1 Data isolation

For every brain-scoped backend operation verify the resolved `brain_id` propagates through:

- ingest;
- graph reads;
- graph writes;
- observations;
- text chunks;
- vectors;
- retrieval/context;
- search;
- entity queries;
- task/job creation;
- task/job listing/status;
- worker execution;
- any cache keys;
- any plugin memory/state that is intended to be brain-specific.

### 11.2 Async jobs

If Celery/tasks carry brain context today, ensure workspace-routed requests enqueue the exact resolved `brain_id`.

Never depend on process-global active workspace state in workers.

### 11.3 Caches

Any cache containing brain-derived data must include `brain_id` in its key.

### 11.4 Browser state

Queries for workspace A must never remain rendered when switching to B.

If using TanStack Query or another cache layer, include workspace/brain ID in all relevant query keys and invalidate/refetch correctly on switch.

---

## 12. Authentication and authorization

Preserve existing BrainPAT behavior.

### System PAT

May:

- list workspaces;
- switch among authorized brains;
- create/update/archive workspaces;
- call each workspace endpoint.

### Per-brain PAT

May:

- access only its associated workspace;
- see only that workspace in the Console;
- use its canonical workspace API URL;
- never switch to another workspace by changing URL manually.

A request with a per-brain PAT to another workspace slug must return `403`, not silently fall back to the permitted brain.

### Do not encode secrets into URLs

Workspace URLs identify the workspace, not the credential.

---

## 13. Backward compatibility

The following should continue to work unless a test proves the current repo deliberately behaves differently:

```text
POST /ingest/
POST /retrieve/context
...
```

with existing BrainPAT + `X-Brain-ID` behavior.

Also preserve current clients that send `brain_id` in bodies where supported.

Avoid breaking the current Python/JS client contracts merely to add path-scoped workspace URLs.

---

## 14. Proposed frontend file changes

Exact names may adapt to existing conventions after inspection.

Likely changes:

```text
console/src/App.tsx
console/src/components/Layout.tsx
console/src/lib/api.ts
console/src/lib/auth.ts                 # only if session migration is needed

console/src/workspaces/
  WorkspaceProvider.tsx
  WorkspaceTabs.tsx
  workspaceRoutes.ts
  types.ts

console/src/pages/WorkspaceSettingsPage.tsx
console/src/components/CreateWorkspaceDialog.tsx
```

Do not introduce a new state library unless the existing stack genuinely requires it.

---

## 15. Proposed backend structure

Codex must inspect the backend package layout before choosing exact paths. Prefer the repository's established naming/conventions.

Conceptual additions:

```text
.../models/workspace.py
.../services/workspaces.py
.../routes/system_workspaces.py
.../routing/workspace_scope.py
.../routes/workspace_api.py
```

Responsibilities:

- `Workspace` model/repository: persistence only;
- workspace service: CRUD + migration + URL construction;
- system routes: management/discovery;
- scope resolver: authorization and brain resolution;
- path-scoped API facade: reuses existing brain-scoped handlers/services.

Do not put business logic directly into React or duplicate route implementation wholesale.

---

## 16. Tests

### 16.1 Backend unit tests

Add tests for:

- slug normalization/validation;
- unique slug and brain ID;
- workspace bootstrap from existing brains;
- idempotent migration;
- URL construction;
- scope resolver precedence;
- conflicting workspace slug vs `X-Brain-ID` rejected;
- per-brain PAT cannot cross workspace;
- system PAT can access multiple workspaces.

### 16.2 Backend integration tests

Create at least two workspaces, e.g. `alpha` and `beta`.

Verify:

1. ingest a unique fact into `alpha` via `/brains/alpha/api/ingest/...`;
2. ingest a different unique fact into `beta`;
3. retrieval in `alpha` cannot retrieve beta-only data;
4. retrieval in `beta` cannot retrieve alpha-only data;
5. equivalent legacy header-scoped request produces the same brain selection;
6. async task created through `alpha` remains alpha-scoped;
7. invalid/archived workspace returns a clear 404/410 according to chosen semantics.

### 16.3 Frontend tests

Verify:

- workspace tabs render from registry;
- route selects correct active workspace;
- switching tabs preserves subpage;
- switching does not trigger `window.location.reload()`;
- page data refetches for the new brain;
- unauthorized workspace route is rejected/redirected safely;
- mobile switcher remains usable;
- create dialog adds/navigates to the new workspace;
- API URL copy value is correct.

### 16.4 Regression tests

Existing BrainAPI tests, Console build, lint/typecheck, and existing API contracts must remain green.

---

## 17. Acceptance criteria

The feature is complete when all of the following are demonstrably true:

- [ ] At least 3 brains can coexist in one running BrainAPI deployment.
- [ ] Each appears as a named Console workspace.
- [ ] Desktop Console exposes them as tabs.
- [ ] Clicking a tab switches without a full browser reload.
- [ ] `/console/w/{slug}/...` is directly addressable/bookmarkable.
- [ ] Switching from one workspace to another retains the current section where possible.
- [ ] Every workspace has a canonical API URL returned by the backend.
- [ ] `POST {workspace_api_url}/retrieve/context` works without `X-Brain-ID`.
- [ ] `POST {workspace_api_url}/ingest/...` scopes writes to that workspace.
- [ ] Conflicting explicit brain scopes are rejected.
- [ ] A per-brain PAT cannot access another workspace by editing the URL.
- [ ] A system PAT can list/switch/manage workspaces.
- [ ] Existing header/body-scoped API clients still work.
- [ ] Existing installations gain workspace records without losing data.
- [ ] No workspace-specific secret appears in a URL or frontend bundle.
- [ ] All relevant tests/build/typecheck/lint pass.

---

## 18. Implementation phases

### Phase 0 — audit and test map

Before changing behavior:

1. identify backend app/router entry points;
2. find the current brain creation/listing source of truth;
3. find the canonical auth/brain-scope dependency;
4. enumerate all public brain-scoped routers;
5. identify persistence used for brain metadata;
6. identify how Celery/job payloads carry brain ID;
7. identify frontend requests that rely on global session `brainId`;
8. run the relevant existing tests and record baseline.

### Phase 1 — workspace registry + discovery

Implement:

- workspace persistence/model;
- idempotent bootstrap from current brains;
- `GET /system/workspaces`;
- `GET /system/workspaces/{slug}`;
- endpoint URL generation;
- management auth.

No destructive changes to existing APIs.

### Phase 2 — path-scoped workspace API

Implement:

- `/brains/{slug}/api/...` routing;
- canonical scope resolver;
- conflict rejection;
- PAT authorization;
- integration tests proving data isolation.

### Phase 3 — URL-addressed Console workspaces

Implement:

- `/w/:workspaceSlug/...` route hierarchy;
- `WorkspaceProvider`;
- API client scoping;
- remove reload-based switching;
- redirects/backward compatibility.

### Phase 4 — workspace tab UX + management

Implement:

- desktop workspace tab strip;
- mobile fallback selector;
- create-workspace UI;
- settings/API endpoint page;
- copy actions.

### Phase 5 — hardening

- full regression suite;
- authorization tests;
- two-brain/three-brain isolation tests;
- proxy/base URL tests;
- deployment smoke test;
- docs/examples.

---

## 19. Deployment considerations

This design must work behind a single public port and reverse proxy.

For Hugging Face Spaces or similar environments, one public origin is enough:

```text
https://<space>.hf.space/console/w/aux-research/
https://<space>.hf.space/brains/aux-research/api/retrieve/context
https://<space>.hf.space/brains/company/api/retrieve/context
```

No dynamic port allocation is required.

If the application has a configurable root path, proxy prefix, or forwarded host mechanism, integrate with it rather than hard-coding origin URLs.

---

## 20. Non-goals for this first implementation

Do not add these unless they are trivial consequences of existing architecture:

- a separate database instance per brain;
- a separate container/process per brain;
- arbitrary custom domains per workspace;
- workspace-to-workspace graph joins;
- workspace sharing/invite/team ACL system beyond existing PAT authorization;
- workspace cloning/export/import;
- per-workspace LLM provider configuration;
- destructive graph deletion UI;
- replacing current BrainPAT auth.

These can be layered on later once workspaces are first-class.

---

## 21. Engineering constraints

- Preserve existing project conventions and public APIs.
- Reuse `brain_id` instead of inventing a second storage partition key.
- Prefer one canonical workspace/brain scope resolver.
- No process-global mutable active workspace.
- No full-page reload for workspace switching.
- No self-proxy HTTP request for workspace facade if router/service reuse is practical.
- No secrets in workspace metadata, API URLs, logs, or frontend state beyond current credential handling.
- Migrations must be idempotent and safe for existing data.
- Add tests before or alongside each behavioral change.
- Update documentation and `.env.example` if `PUBLIC_BASE_URL` or another setting is added.

---

## 22. Deliverables

The implementation PR should contain:

1. workspace registry/model + migration/bootstrap;
2. workspace management/discovery API;
3. path-scoped hosted API facade;
4. canonical scope/auth resolution;
5. URL-addressable Console workspace routing;
6. tabbed workspace switcher;
7. create/manage workspace UI;
8. per-workspace endpoint display/copy UX;
9. isolation, authorization, frontend, and regression tests;
10. documentation with curl examples and deployment URL behavior.

