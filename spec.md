BrainAPI Unified Authentication, Credential Registry, Console Login & Runtime Hardening
Status: Implementation specification  
Repository: `JsonLord/brainapi2`  
Implementation branch: `feat/unified-auth-runtime`  
Source PR: #8, branch `jules-4649944271521841670-0d5c9ccf`  
Verified implementation base: `eb08a1549754fab8ec2459bb4472644630d4cb99`  
Original handoff PR head: `784fecf82694cf5646d247836eb3f173b4a05941`  
Base registry fix: `fix/authoritative-brain-registry` at `42cde77950db2e24a1b7acbc3477274bd9cad06a`  
Date: 2026-10-21
---
0. Executive decision
BrainAPI must stop treating a Brain, an access credential, a browser login session, and an administrator credential as the same concept.
Target model:
```text
SYSTEM_PAT
  └── one stable deployment-level administrator credential
      ├── administer brains/workspaces
      ├── create/revoke scoped access tokens
      ├── access any workspace
      └── qualify the service

Brain / Workspace
  └── durable resource identity
      ├── no automatically generated credential
      └── no credential stored as part of resource identity

AccessToken
  └── explicit named credential
      ├── scoped to one brain/workspace
      ├── explicit permissions
      ├── hash at rest
      ├── plaintext shown once on creation
      └── revocable independently of the brain

ConsoleSession
  └── short-lived browser session
      ├── created after an access token is verified
      ├── opaque HttpOnly Secure cookie
      ├── never persists SYSTEM_PAT or scoped token in browser storage
      └── may have no selected workspace until the human chooses one
```
This pass must also fix the runtime defects observed in live Hugging Face testing:
split `SYSTEM_PAT` / `BRAINPAT_TOKEN` semantics;
console rejects a valid system credential because login validation hits `/meta/entity-labels` against a fake `default` brain;
every Brain automatically generates a fresh PAT;
generated PATs can leak through SQL/error logs;
invalid structured-ingest requests become HTTP 500 because validation errors contain a non-JSON-serializable `ValueError`;
failed ingestion causes repeated `/tasks/None` polling;
trace queues fill while probing unused Mongo/Neo4j/Milvus/RabbitMQ services;
HF memory runs on ephemeral filesystem when `/data` is absent without a strong release gate;
secondary hardening warnings: Celery as root, PostgreSQL `SQL_ASCII`/trust defaults, unresolved Pydantic warning.
The registry-authority fixes from `42cde779...` and the qualified EmbeddingGemma 2 / 512d vector contract are invariants and must not regress.
---
1. Non-negotiable invariants
1.1 Registry authority
The persisted Brain registry is authoritative.
An unknown brain read must return:
```text
404 BRAIN_NOT_FOUND
```
and must not create:
Brain rows;
Workspace rows;
PostgreSQL databases/schemas;
vector indexes;
graph state;
Redis application state.
This includes semantically read-only POSTs such as `/retrieve/search` and `/retrieve/context`, plus Agent API and MCP equivalents.
1.2 Credentials are not resource identity
Creating a Brain or Workspace MUST NOT implicitly create a credential.
Deleting/archiving a resource MUST NOT rotate unrelated credentials.
Rotating/revoking a credential MUST NOT change Brain or Workspace identity.
1.3 One canonical administrator secret
`SYSTEM_PAT` is the canonical deployment-level administrator secret.
HTTP, MCP, console, qualification scripts and optional OAuth must all resolve system identity through the same auth service.
1.4 No default administrator token
Remove every usable fallback such as:
```python
os.getenv("SYSTEM_PAT", "brainpat_default_token")
```
Missing administrator credentials must fail closed in production.
1.5 Plaintext-token rule
Plaintext credentials may exist only:
while being verified;
while a newly created scoped token is returned once;
as the injected `SYSTEM_PAT` secret.
They must never be:
stored in Brain rows;
returned by Brain/workspace listing APIs;
written to logs, traces, SQL errors or exceptions;
stored in browser `localStorage`/`sessionStorage`;
committed to repository files.
---
2. Current defects to replace
At PR #8 head, system auth is split approximately as follows:
```text
BrainPATMiddleware      -> SYSTEM_PAT
/meta/login-info        -> SYSTEM_PAT
console copy            -> SYSTEM_PAT
Config.brainpat_token   -> BRAINPAT_TOKEN
entrypoint bootstrap    -> BRAINPAT_TOKEN
HF env example          -> BRAINPAT_TOKEN
MCP legacy/system path  -> BRAINPAT_TOKEN
```
This is invalid because it can create two administrator credentials.
The console also currently treats system auth as:
```json
{"is_system_pat": true, "brain_id": "default"}
```
then calls `/meta/entity-labels` to validate login. With `DEFAULT_BRAIN_FALLBACK=false`, a valid system token may authenticate and still be rejected by the UI because no `default` Brain exists.
The current `Brain` model also auto-generates `pat` with Python `random.choice`; this must be removed from new resource creation.
---
3. Central AuthService
Create one authoritative auth service. Exact module layout may follow repo conventions, but all surfaces must delegate to it.
Suggested conceptual API:
```python
class AuthService:
    def authenticate_token(self, token: str) -> Principal: ...
    def authorize_workspace(self, principal: Principal, brain_id: str, permission: str) -> None: ...
    def create_access_token(self, ...) -> IssuedToken: ...
    def revoke_access_token(self, token_id: str) -> None: ...
    def list_access_tokens(self, ...) -> list[AccessTokenMetadata]: ...
    def create_console_session(self, principal: Principal) -> ConsoleSession: ...
    def resolve_console_session(self, cookie: str) -> Principal: ...
    def revoke_console_session(self, cookie: str) -> None: ...
```
These must use it:
`BrainPATMiddleware`;
`/auth/*`;
`/meta/login-info` compatibility route;
workspace Agent API;
workspace MCP;
root/legacy MCP while retained;
system/workspace admin routes;
console;
qualification scripts.
After this refactor, route/middleware code must not perform independent system-token environment comparisons.
---
4. Canonical SYSTEM_PAT and compatibility migration
Resolve secrets at startup as follows:
```text
SYSTEM_PAT set, BRAINPAT_TOKEN unset
    -> use SYSTEM_PAT

SYSTEM_PAT unset, BRAINPAT_TOKEN set
    -> temporarily use BRAINPAT_TOKEN as legacy alias
    -> emit one deprecation warning, never the value

both set and identical
    -> use SYSTEM_PAT

both set and different
    -> FAIL STARTUP with SYSTEM_PAT_CONFLICT

neither set
    -> production: FAIL STARTUP
```
There must never be two simultaneously valid system administrator tokens.
Expose one resolved config field:
```python
config.system_pat
```
Any temporary `config.brainpat_token` compatibility property must resolve to the same canonical value and be marked deprecated.
Update:
`entrypoint.sh`;
`deploy/env.hf.cpu.example`;
`Agent.md`;
console help copy;
MCP code;
qualification scripts.
---
5. Principal model
Introduce an explicit principal.
Example:
```python
@dataclass(frozen=True)
class Principal:
    kind: Literal["system", "access_token", "console_session"]
    subject_id: str
    brain_id: str | None
    permissions: frozenset[str]
    token_id: str | None = None
```
System principal:
```text
brain_id = None
permissions includes system:admin
```
Scoped principal:
```text
brain_id = lifeos-projects
permissions = explicit scoped permissions
```
A system principal MUST NOT be represented as `brain_id="default"`.
---
6. Explicit scoped access-token registry
6.1 Data model
Persist explicit access tokens in the system PostgreSQL database.
Minimum fields:
```text
id
label
brain_id
token_prefix
token_hash
permissions
created_at
last_used_at
expires_at
revoked_at
legacy
created_by
```
Do not store plaintext.
6.2 Token generation
Generate with `secrets`, e.g.:
```python
plaintext = "bpat_" + secrets.token_urlsafe(32)
```
Persist a cryptographic hash and a safe display prefix.
6.3 Permission vocabulary
Minimum target vocabulary:
```text
memory:read
memory:write
brain:read
system:admin
```
If granular route mapping is too large for the first pass, `brain:read` / `brain:write` may be used initially, but permission storage and centralized authorization must exist now.
6.4 Resource creation no longer creates credentials
These create resources only:
```text
POST /system/brains
POST /system/workspaces
```
They MUST NOT generate or return a PAT.
6.5 Token-management endpoints
System administrator only:
```text
GET    /system/access-tokens
POST   /system/access-tokens
DELETE /system/access-tokens/{token_id}
```
Example create request:
```json
{
  "brain_id": "lifeos-projects",
  "label": "hermes-prod",
  "permissions": ["memory:read", "memory:write"]
}
```
Creation response may return the new plaintext token exactly once:
```json
{
  "id": "...",
  "label": "hermes-prod",
  "brain_id": "lifeos-projects",
  "permissions": ["memory:read", "memory:write"],
  "token_prefix": "bpat_ab12...",
  "token": "bpat_<plaintext-shown-once>"
}
```
List endpoints NEVER return plaintext or token hash.
---
7. Legacy per-brain PAT migration
Existing stored Brain PATs must remain temporarily usable so current agents are not locked out.
During the compatibility window, `AuthService.authenticate_token()` may check:
canonical system PAT;
new access-token registry;
legacy persisted Brain PAT.
Legacy PAT rules:
own Brain only;
never system privileges;
rate-limited deprecation warning without token value;
internally identifiable as legacy;
no new Brain receives an automatic legacy PAT.
Provide a migration/rotation helper that reports Brain name and migration status without printing plaintext.
Any token previously emitted in SQL/application logs must be considered exposed and rotated after the new registry is available.
---
8. Browser console authentication
The console must stop keeping plaintext PATs in browser storage.
8.1 Endpoints
Add:
```text
POST /auth/session
GET  /auth/me
POST /auth/logout
```
`POST /auth/session` accepts a token over TLS, verifies it through `AuthService`, creates a random opaque server-side Redis session, and returns an HttpOnly cookie.
Cookie requirements:
```text
HttpOnly
Secure
SameSite=Strict
Path=/
configurable TTL, default ~8h
```
Do not echo the submitted token.
8.2 CSRF
Cookie-authenticated unsafe browser requests must use a CSRF token associated with the server session and sent in `X-CSRF-Token`.
Direct Bearer/BrainPAT agent requests do not use browser CSRF.
8.3 `/auth/me`
System response:
```json
{
  "authenticated": true,
  "kind": "system",
  "is_system": true,
  "brain_id": null,
  "permissions": ["system:admin"]
}
```
Scoped response:
```json
{
  "authenticated": true,
  "kind": "access_token",
  "is_system": false,
  "brain_id": "lifeos-projects",
  "permissions": ["memory:read", "memory:write"]
}
```
8.4 Console storage
Remove token plaintext from `AuthSession` / `sessionStorage`.
The console may store non-secret UI state such as API base URL and last workspace slug.
8.5 Login UI
Rename human-facing `BrainPAT` field to `Access token`.
Explain two token classes:
```text
Administrator token
    SYSTEM_PAT configured by deployment operator

Workspace token
    explicit named scoped token created for an integration/agent
```
Do not tell users that Brain creation returns a token.
8.6 System login flow
Correct flow:
```text
POST /auth/session
GET  /auth/me              -> system, brain_id=null
GET  /system/workspaces
select valid workspace
```
Use last valid workspace if present; otherwise first active workspace; if none exist, show admin empty state.
DO NOT call `/meta/entity-labels` to decide whether login succeeded.
DO NOT invent a `default` Brain.
8.7 Scoped login
A scoped token login:
resolves the token's Brain;
discovers only authorized workspace(s);
selects its workspace;
hides system/workspace-admin controls.
---
9. Agent and MCP authentication
New integrations should prefer:
```http
Authorization: Bearer <token>
```
Keep:
```http
BrainPAT: <token>
```
as compatibility transport.
All validation delegates to `AuthService`.
For `/brains/{workspace_slug}/...`:
```text
system principal              -> allowed
matching scoped token         -> allowed according to permission
valid different-brain token   -> 403 FORBIDDEN
unknown/revoked token          -> 401 AUTH_INVALID
```
Workspace MCP must accept the SAME canonical system PAT as HTTP and console. It must not use independent `BRAINPAT_TOKEN` administrator semantics.
Legacy root MCP may remain but must use the same service.
---
10. Compatibility `/meta/login-info`
Keep temporarily for old clients as a thin view over `/auth/me`.
System:
```json
{"is_system_pat": true, "brain_id": null}
```
Scoped:
```json
{"is_system_pat": false, "brain_id": "lifeos-projects"}
```
Mark deprecated. Never return `"default"` for system auth.
---
11. Structured-validation error serialization
Current failure:
```text
RequestValidationError
  -> exc.errors()
  -> ctx.error contains ValueError
  -> JSONResponse serialization fails
  -> HTTP 500
```
Required behavior:
```text
invalid request
-> HTTP 422
-> JSON-safe VALIDATION_ERROR envelope
```
Sanitize error details using an appropriate FastAPI/Pydantic JSON encoder or a safe recursive encoder.
Expected shape:
```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "data", 0],
      "msg": "Value error, direct triples require subject, subj_event, and object",
      "ctx": {"error": "direct triples require subject, subj_event, and object"}
    }
  ],
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "resolution": "Correct the fields listed in detail and retry.",
    "request_id": "..."
  }
}
```
Regression-test both root and workspace structured-ingest routes.
---
12. Stop `/tasks/None` polling
Repository clients/console may poll task status only when:
```text
HTTP == 202
AND task_id is a non-empty string
```
For 4xx/5xx:
render the structured error;
do not start polling.
For malformed 2xx without task ID:
fail client-side with a deterministic contract error;
do not request `/tasks/None`, `/tasks/null`, etc.
Add a regression test proving failed ingestion generates zero task-status requests.
---
13. Route discovery
Do not add arbitrary aliases solely to hide caller mistakes.
Document canonical routes and mark observed wrong patterns as invalid.
Canonical examples:
```text
POST /system/brains
POST /system/workspaces
GET  /brains/{slug}/agent/capabilities
GET  /brains/{slug}/api/openapi.json
GET  /brains/{slug}/agent/openapi.json
```
Invalid/obsolete examples seen during probes:
```text
POST /brains
POST /system/brains/{id}
GET  /system/brains/{id}
GET  /agent/capabilities
GET  /brains/{slug}/api/meta/login-info
```
Clients should discover routes from workspace discovery/OpenAPI instead of guessing paths.
---
14. Runtime tracing hardening
14.1 Probe only active dependencies
HF profile should monitor:
```text
PostgreSQL  yes
Redis       yes
Ollama      yes
```
Probe MongoDB/Neo4j/Milvus/RabbitMQ only when configured as active backends.
14.2 Queue consumer invariant
No trace consumer/sink must mean no indefinitely accumulating queue.
Acceptable designs:
do not enqueue when no subscriber exists;
install a bounded logging subscriber;
explicit `TRACE_MODE=disabled|log|queue`.
HF default must not fill the 10k queue and spam one warning per dropped event.
14.3 Saturation behavior
If drops occur:
rate-limit warning logs;
expose dropped-event count;
do not emit an endless warning storm.
---
15. Persistence state and release gate
BrainAPI is a memory service. Ephemeral state must be first-class and visible.
Expose:
```text
PERSISTENCE_MODE=durable|ephemeral
```
auto-detected from configured durable root.
Health/readiness should include non-secret persistence state.
Example:
```json
{
  "status": "degraded",
  "service": "brainapi",
  "persistence": {
    "mode": "ephemeral",
    "root": "/app/.data/brainapi"
  }
}
```
Production without durable storage may start only with an explicit staging/recovery override such as:
```text
ALLOW_EPHEMERAL_STORAGE=true
```
but then:
health remains degraded;
console shows a persistent warning;
production release qualification is NO-GO.
Do not break local development defaults unnecessarily.
---
16. Secondary hardening
Implement where safe in this pass; otherwise report explicitly as follow-up blockers.
PostgreSQL
new clusters should use UTF-8 rather than `SQL_ASCII`;
prefer safe local password/SCRAM auth over broad trust if practical;
do not destructively reinitialize existing clusters.
Celery
Run worker as a dedicated unprivileged user rather than root.
Pydantic warning
Resolve the unresolved `lifespan` forward-reference warning or identify its upstream owner. Do not globally suppress warnings.
---
17. Console access-token UX
System administrators get an `Access tokens` section in workspace settings.
Show only metadata:
```text
Label
Token prefix
Permissions
Created
Last used
Expiry
Status
```
Actions:
```text
Create token
Revoke token
```
Creation must warn:
```text
This token is shown once. Copy it now.
```
No reveal-existing-token function.
Never show `SYSTEM_PAT` in the UI.
Scoped users must not see token administration unless explicitly authorized.
---
18. Brain/workspace response audit
These must never return credential plaintext:
```text
/system/brains-list
/system/workspaces
/system/workspaces/{slug}
workspace discovery
console bootstrap
```
Remove/deprecate `pat` from client-visible Brain models.
A legacy DB `pat` column may remain temporarily during migration but must not leak through serialization.
---
19. Logging/redaction
Create a reusable secret-redaction policy for:
```text
SYSTEM_PAT
Authorization
BrainPAT
new access-token plaintext
legacy Brain PAT
Postgres password
Redis password
Celery URL credentials
```
Do not debug auth by logging token values.
Database errors must no longer contain plaintext token INSERTs because new token storage contains only hashes.
---
20. Required tests
20.1 Config resolution
```text
SYSTEM_PAT only                 -> success
legacy BRAINPAT_TOKEN only      -> success + deprecation
both identical                  -> success
both different                  -> SYSTEM_PAT_CONFLICT / startup failure
neither in production           -> failure
no default admin token          -> proven
```
20.2 Auth parity
Across root HTTP, workspace API, Agent API, workspace MCP and retained legacy MCP:
```text
SYSTEM_PAT             -> system access
scoped new token       -> own brain allowed
scoped new token       -> other brain 403
unknown token          -> 401
missing token          -> 401
revoked token          -> 401
legacy Brain PAT       -> own brain allowed during migration
```
20.3 Console login
System:
```text
POST /auth/session -> success
GET /auth/me       -> brain_id null
GET /system/workspaces -> success
no /meta/entity-labels validation required
```
Scoped:
```text
/auth/session -> success
/auth/me      -> correct brain
workspace discovery -> only authorized workspace
```
Invalid credential yields 401 and no session cookie.
Logout invalidates the server session.
20.4 Browser secret handling
Prove plaintext tokens are absent from:
```text
localStorage
sessionStorage
URL
persisted React state after login
```
20.5 Token lifecycle
```text
create Brain -> no token generated
create Workspace -> no token generated
create access token -> plaintext once
list tokens -> no plaintext/hash
use token -> success
revoke -> subsequent auth fails
```
20.6 Validation serializer
Malformed structured ingestion on root and workspace API:
```text
422 VALIDATION_ERROR
JSON serializable
no exception object
```
20.7 Task polling
Failed ingestion generates zero `/tasks/None`/`null` requests.
20.8 Trace monitoring
HF profile:
```text
probes Postgres, Redis, Ollama
not Mongo/Neo4j/Milvus/RabbitMQ
queue remains bounded/drained over simulated time
```
20.9 Persistence
Test durable and ephemeral modes; ephemeral production is visibly degraded and cannot receive production GO.
20.10 Preserve existing gates
Rerun:
authoritative registry B1-B4 tests;
real PostgreSQL/Redis integration;
EmbeddingGemma qualification.
Embedding requirements remain:
```text
embeddinggemma-2:270m
native 768
stored 512
all five pgvector schemas vector(512)
no runtime model pull
```
---
21. Implementation order
Phase A — central auth resolution
Add central `AuthService` / Principal model.
Resolve `SYSTEM_PAT` plus legacy alias.
Switch middleware, meta and MCP to it.
Add config/auth tests.
Keep legacy Brain PAT validation.
Phase B — console sessions
Add `/auth/session`, `/auth/me`, `/auth/logout`.
Redis-backed opaque sessions + CSRF.
Remove plaintext PAT browser persistence.
Remove fake default Brain.
Remove `/meta/entity-labels` login validation.
Phase C — explicit access-token registry
Add token persistence.
Add create/list/revoke APIs.
Cryptographic generation + hash at rest.
Stop automatic `Brain.pat` creation.
Add admin token-management UI.
Phase D — legacy migration
Preserve legacy validation.
Add migration/rotation tooling.
Document exposed-token rotation.
Phase E — runtime correctness
Fix 422 serialization.
Fix null task polling.
Fix trace monitoring/queue behavior.
Add persistence degraded state.
Apply safe secondary hardening.
Phase F — qualification
focused deterministic tests;
real PostgreSQL/Redis integration;
full non-integration suite;
full production Docker build;
offline EmbeddingGemma qualification;
exact-SHA HF deployment;
live acceptance.
---
22. Live HF acceptance gate
Do not claim completion from local tests alone.
Deploy one exact candidate SHA and record both GitHub candidate and HF source revision.
Authentication
```text
SYSTEM_PAT:
  /auth/session -> success
  /auth/me -> system, brain_id=null
  /system/workspaces -> 200
  console login -> success
  workspace MCP -> success
```
There must be no second administrator secret.
Scoped token
Create a temporary named token for a disposable workspace:
```text
own Agent API -> success
own Native API -> success
own MCP -> success
other workspace -> 403
revoke token -> subsequent request 401
```
Registry regression
```text
unknown entities -> 404 BRAIN_NOT_FOUND
unknown search -> 404 BRAIN_NOT_FOUND
unknown context -> 404 BRAIN_NOT_FOUND
```
Registry/storage/cache unchanged.
Validation
Malformed structured ingest:
```text
422 VALIDATION_ERROR
```
not 500.
UI/client must not poll a task after that failure.
Tracing
Observe multiple monitor intervals:
no queue-full storm;
no DOWN spam for unused dependencies.
Persistence
If runtime remains ephemeral:
```text
release = DEGRADED / NOT PRODUCTION DURABLE
```
---
23. Out of scope
Do not redesign:
EmbeddingGemma model choice;
768 -> 512 Matryoshka/L2 pipeline;
retrieval/reranking architecture;
workspace URL authority;
B1-B4 registry architecture;
general agent orchestration;
external LifeOS behavior beyond documented client contracts.
Do not introduce a second identity provider merely to avoid fixing token semantics.
Optional MCP OAuth may remain, but it must resolve principals through the same central auth service.
---
24. Documentation terminology
Update `Agent.md`, HF env example, hosted-use docs, OpenAPI descriptions and console copy.
Canonical terms:
```text
SYSTEM_PAT
    deployment administrator secret

Access token
    named scoped credential for one Brain/Workspace

Console session
    temporary browser session derived from verified credentials

Brain
    memory namespace

Workspace
    hosted routing/display facade for a Brain
```
The `BrainPAT` header may remain as compatibility transport, but do not call every credential a BrainPAT in human-facing UX.
---
25. Required final report
Return:
```text
branch
base SHA
final SHA
files changed

auth architecture implemented
SYSTEM_PAT migration behavior
legacy BRAINPAT_TOKEN behavior
legacy per-brain PAT behavior
new access-token schema
console session behavior

test counts/results:
  config/auth
  console
  token lifecycle
  migration
  validation 422
  task polling
  tracing
  persistence
  registry regression
  embedding qualification
  integration
  full suite

production Docker build result
HF deployed source revision
live auth results
live scoped-token results
live registry results
live 422 result
trace result
persistence mode

known exposed-token rotation status
remaining blockers
production GO/NO-GO
```
Do not report production GO while persistence is ephemeral.
Do not report auth GO unless HTTP API, console and MCP all accept the same canonical system administrator credential.
---
26. Definition of done
```text
[ ] exactly one canonical system administrator credential exists
[ ] SYSTEM_PAT works consistently in HTTP, console and MCP
[ ] legacy BRAINPAT_TOKEN cannot become a second independent admin secret
[ ] missing system secret fails closed
[ ] no usable default admin token exists

[ ] system console session has brain_id=null until workspace selection
[ ] console does not validate login through /meta/entity-labels
[ ] console does not persist plaintext PAT/token in browser storage

[ ] Brain creation does not generate a credential
[ ] Workspace creation does not generate a credential
[ ] scoped tokens are explicit, named, revocable and hashed at rest
[ ] plaintext scoped token is returned only once
[ ] Brain/workspace listings expose no token plaintext

[ ] old per-brain PATs temporarily work only for their own Brain
[ ] exposed legacy tokens have a rotation path

[ ] malformed requests return JSON-safe HTTP 422
[ ] failed ingestion never causes /tasks/None polling

[ ] trace monitor checks only active dependencies
[ ] trace queue cannot silently fill forever without a consumer

[ ] persistence mode is explicit
[ ] ephemeral persistence prevents production GO

[ ] authoritative registry regression tests pass
[ ] EmbeddingGemma 768->512 qualification passes
[ ] full production image builds
[ ] exact candidate SHA is deployed and verified live
```

## GitHub content checkpoints

Content backup runs before WorkspaceService.bootstrap and before serving. It
preserves canonical Brain IDs/workspaces and logical PostgreSQL data/graph/vector
contents with the full EmbeddingGemma index identity. Authentication registries,
legacy PATs, SYSTEM_PAT/GITHUB_PAT, token hashes, console/CSRF/Redis state and
internal passwords are excluded. Empty local data may restore a fully validated
snapshot; populated databases with unknown/divergent lineage fail closed rather
than being overwritten. Only registered Brains are exported. Periodic debounced
checkpoints and a bounded graceful-shutdown attempt use non-force Git pushes,
checksums and an operation lock. Existing authoritative registry, unified-auth,
side-effect-free missing-Brain reads and 768→512 embedding contracts continue to
apply. See docs/content-backup.md for format, recovery, variables and RPO limits.
