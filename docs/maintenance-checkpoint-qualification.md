# Maintenance checkpoint and RPO qualification

Runtime candidate: `ea2acb63d78204841c96d86011aeb5da72a6d617`, branch
`feat/unified-auth-runtime`, based on `9ba18c51781547754e8482598edfaf7898808529`.
Qualification date: 2026-10-23 UTC.

## Implementation

Access-log redaction preserves tuple/mapping structure and format placeholders,
redacts credential components before interpolation, and omits access-log query
values. A real Uvicorn AccessFormatter self-test participates in readiness.

Administrator-only `/system/backup/{quiesce,checkpoint,status,resume}` uses
cross-process request leases and opaque Celery delivery/execution accounting.
Accepted work, descendants, retries and duplicate running deliveries must settle
before the gate seals. Publication uncertainty/drain/remote failures keep writes
held. Successful checkpoint publication is followed by remote HEAD and complete
manifest/checksum verification. The manifest carries a non-secret handoff hold;
replacement containers keep it until explicit resume. Periodic/shutdown work
only verifies a sealed checkpoint, preventing late publication behind the
operator's SHA. Explicit resume publishes a verified hold release before reopening.

Startup re-fetches after restore and again after workspace bootstrap, failing
closed on remote advances. Content database/table allowlists, logical PostgreSQL
format, canonical registry, auth separation and embedding profile are unchanged.

Readiness exposes local storage separately from GitHub content recoverability,
restored/remote checkpoint, lag indicator, dynamic dirty state, successful
checkpoint/reconciliation time and age. Maximum RPO defaults to interval=300;
`ALLOW_CHECKPOINTED_PRODUCTION=false` remains the default. True permits readiness
only with qualified restore/current remote lineage, fresh verification and healthy
auth/database/embedding/access logging. `zero_loss_durable=false` always remains
explicit. Abrupt termination can lose writes since the last successful checkpoint.

## Local results

- Focused backup/maintenance/redaction/runtime tests: 70 passed.
- Final full non-integration suite: 795 passed, 22 skipped, 3 deselected,
  16 subtests passed. The same four previously established failures remain:
  `OpenApiContractTests::test_operations_are_function_calling_ready`,
  `ParentCompletionChainTests::test_parent_does_not_mark_completed_before_chain`,
  `ExecuteReadQueryTests::test_applies_row_cap_and_truncated_flag`, and
  `test_workspace_plugin_route_registration_and_collision`.
- Network-isolated actual entrypoint qualification passed auth, registry,
  EmbeddingGemma native 768 / normalized 512, all five store dimensions,
  worker ingestion, retrieval and logical dump/drop/restore.
- Exact committed source Docker build passed; image
  `sha256:1b3bb78b149398a65adad5df58a7ccc5d952db2838b9c08e954deb353e285aef`.
  The final image, with no source mounts, passed all four entrypoint auth,
  registry, EmbeddingGemma and logical backup qualifications.
- Final sealed handoff checkpoint `ec1f9615576d87de98f061c5fb71eabb631c1323`
  remained unchanged through graceful shutdown. The first fresh container restored
  that exact SHA, the same identities and unique fact, and inherited the hold.
  Search/context succeeded before explicit resume; no second restart was needed.
- A local explicit RPO opt-in produced `production_ready=true` with all checks
  healthy and `zero_loss=false`. The default opt-out produced false. A simulated
  Git outage changed readiness to false; restoring connectivity restored true.
  Disposable deletion was checkpointed and writes resumed.

No real GitHub credential is substituted in the isolated local fixture; actual
Space GitHub access is qualified separately.

## Hugging Face qualification

The initial upgrade paused the old empty Space before source/variable changes.
No registered Brains or active workspaces existed; the previous valid checkpoint
was `eef37a45d3d1ee7f1776d363d1b42d60e4b3eb15`.

HF source commit: `89e57b7f3eab1186d70beda15b0023a4c8f64a0a`.
All 729 committed source files were verified against their uploaded Git/LFS
content IDs. The complete HF Docker build and initial startup passed. Canonical
SYSTEM_PAT, remote GitHub access, database, embedding and access logging checks
were healthy. The live production RPO opt-in remains false.

Disposable accepted ingestion was immediately followed by the barrier; work
settled, search/context returned its unique fact, new writes were rejected, and
checkpoint A was `404b8a047d4704b9408f2551344befec11858441`.
An independent Git fetch validated the complete manifest, checksums, canonical
registry, source SHA and EmbeddingGemma metadata. In-memory pg_restore inspection
found the fact and no known local credential, token signature, auth/session table
or internal password material. Space-only secret values were never read back.

The factory rebuild completed every Docker stage and image publication. During
build, the old runtime remained sealed on A and remote HEAD remained A. The first
replacement startup ID was `9fa8fac7-4138-4676-a12f-8d0352d923ec`, distinct from
`f947743b-6f40-49ea-b652-9fa22f85c4f5`. It restored **exactly A**, re-fetched A,
reported lag zero, inherited the hold and passed all dependency checks. The same
workspace and canonical Brain identities survived. Search and context immediately
returned the unique fact on that first boot; **no second restart** was required.
Explicit resume published a verified release and a subsequent content mutation
succeeded.

Live HTTP/console/MCP auth, console CSRF/logout, unknown Brain side-effect-free
404, duplicate create 409, malformed ingest 422 without task polling, scoped-token
403 for all four maintenance endpoints, and invalid-auth 401 precedence passed.
The disposable scoped credential was revoked immediately; values stayed in memory.

An actual access-log query canary produced `GET /health?[REDACTED] HTTP/...`.
The raw current-run stream was inspected in memory: no query canary, known local
credential, recognizable token signature, Uvicorn formatting exception or
`queue.Full` was observed. Space-only secret values were never fetched for scanning.

Only `maintenance-qa-d4646fa9f971` was deleted and its workspace archived. Deletion
checkpoint: `6557f02506da137ef69ff0812fe5b1b4d7c1a58a`.
Explicit release/final remote checkpoint:
`a64d5e08d8ff6bd69372cd326120a61bea940de8`.
Independent fetch/checksum/profile validation passed with zero registered Brains
and no database archives. Git ancestry verifies A → deletion → final release;
there was no force-push or divergent-writer merge. Final runtime: writes enabled,
no pending tasks, backup ready/clean, all five dependency checks healthy.

## Changed surfaces and operating contract

- Redaction: `src/services/redaction.py`; realistic formatter and credential
  regression cases in `tests/test_maintenance_checkpoint.py`.
- Barrier: backup `maintenance.py`, `tasks.py`, `middleware.py`, admin API route,
  HTTP/MCP application wiring and Celery task base.
- Recovery/readiness: backup manager/daemon/Git/state/health, public health route,
  persistence policy and entrypoint startup ordering.
- Operator material: `Agent.md`, `spec.md`, `docs/content-backup.md`, HF environment
  example and `scripts/qualify_maintenance.py`.

Logical backup format remains v1: canonical credential-free registry/workspaces,
chunked PostgreSQL custom archives and checksummed manifest, with a validated
non-secret maintenance hold field. Auth/token/session registries, Redis and PGDATA
remain excluded. Git credentials stay in process environment through temporary
askpass plumbing, never remote URLs or repository files.

Restore runs before workspace bootstrap, followed by another remote HEAD check
before Supervisor/API/worker startup. A remote advance fails closed; recovery must
restart against current HEAD. The maintenance hold survives a fresh restore.

Normal runtime uses dirty/debounced snapshots at 300 seconds with a final shutdown
attempt. A **sealed handoff** permits only verification from periodic/shutdown work;
only explicit administrator checkpoint/resume may publish a successor. Unchanged
content reconciliation refreshes verification freshness without redundant commits.

## Release assessment

Source commits were pushed and the runtime candidate was independently verified
at `JsonLord/brainapi2:feat/unified-auth-runtime`; HF uploaded content was verified
file-by-file. Real Space GitHub checkpoint push/fetch qualification passed.
No Space secret was changed or copied from this environment.

**PRODUCTION NO-GO under the current policy:**
`ALLOW_CHECKPOINTED_PRODUCTION=false` remains explicit. With the local test's true
opt-in and healthy fresh checkpoint, readiness was true; stale or unreachable
checkpoints reject readiness. The declared maximum RPO is **300 seconds** and
`zero_loss_durable=false`. Unexpected container death can lose writes since the
last successful checkpoint; overdue/failed backup increases that exposure and
must make readiness false. This work does not implement synchronous durability.

The requested functional live qualification passed. Remaining release considerations
are explicit operator acceptance of checkpoint RPO and the four independently
established baseline suite failures listed above. No additional runtime/build
failure was observed.
