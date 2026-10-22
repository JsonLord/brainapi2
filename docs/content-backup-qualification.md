# Content backup qualification

Qualified on `feat/unified-auth-runtime`, based on unified-auth commit
`281c4ac9eac90204c9f47795fbacc5011937684b`. No Hugging Face deployment or production
Brain mutation was performed.

- Full non-integration suite: **761 passed, 22 skipped, 3 deselected, 16 subtests
  passed**, with the same four independently established baseline failures:
  `OpenApiContractTests::test_operations_are_function_calling_ready`,
  `ParentCompletionChainTests::test_parent_does_not_mark_completed_before_chain`,
  `ExecuteReadQueryTests::test_applies_row_cap_and_truncated_flag`, and
  `test_workspace_plugin_route_registration_and_collision`.
- Backup-specific tests: **20 passed**. Includes real local bare-Git refs,
  unchanged-content suppression, restart, conflict/corruption/profile/PG-version
  rejection, auth/secret protection, cross-process dirty generation, current-start
  gate, first boot from a README-only repository, deleted-manifest rejection, missing-PAT CLI failure, and final shutdown checkpoint scheduling.
- Actual PostgreSQL 15 + pgvector round-trip: random test Brains and unique facts,
  all five index profiles, legacy PAT stripping, access-token registry exclusion,
  unregistered database exclusion, dirty tracking (including auth-only writes and
  rollback), same IDs/workspaces/BM25 results after dropping/restoring test DBs,
  profile mismatch rejection, no-op deduplication, and a new content commit.
- Real Ollama `embeddinggemma-2:270m` was baked into the CPU model image. Actual
  search/context controllers returned both restored facts without mock embeddings
  or application-start model pulls.
- Production Docker dependency/runtime image build succeeded. Final source,
  scripts and deploy configuration were then mounted read-only for isolated
  container qualification; this is not an exact-commit deployable image claim.
- Actual entrypoint + Supervisor qualification: missing `GITHUB_PAT` exited before
  bootstrap/serving. A synthetic local bare-Git fixture restored before bootstrap
  and API startup. Authenticated HTTP search/context returned both restored Brains'
  facts and canonical IDs. This container had `--network none`; its Git command
  adapter redirected only the configured backup URL into a private test repository.
  The credential was an explicit test sentinel, so this does **not** qualify the
  real GitHub PAT or GitHub publication.
- Graceful shutdown stopped writers, checkpointed, then stopped PostgreSQL. Restart
  kept the local data with matching checkpoint lineage; the worker reached RUNNING.
  Role provisioning on repeated start produced neither an existing-role error nor
  password-bearing SQL in server logs.
- Existing live auth qualification passed: HTTP/MCP parity, console CSRF/session
  logout, revocation, legacy migration, hash-only token metadata, credential-free
  resources, bounded tracing and request validation.
- Existing live authoritative-registry qualification passed: unknown/orphan reads
  return side-effect-free 404, duplicate/concurrent create is a conflict, IDs stay
  stable, and supported clear/delete operations work.

GitHub backup-repository **dry-run** push was accepted; no ref was written.
A transient GitHub authentication failure during fetch resolved on the final
source push. Feature commit `852b38191fefdc2aea4d2eea3451286b87dfa848` was pushed
successfully to `JsonLord/brainapi2:feat/unified-auth-runtime` using a normal
fast-forward push. Actual backup-repository publication using the real Space
`GITHUB_PAT` remains unqualified: that secret is not injected into this workspace,
and no deployment was authorized. Do not substitute `SYSTEM_PAT` or `HF_TOKEN`.

The model/auth/registry contracts remain unchanged. Async checkpoint RPO and
explicit degraded ephemeral-storage health remain applicable; see
[recovery instructions](content-backup.md).
