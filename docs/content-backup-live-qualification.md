# Hugging Face content backup live qualification

Target: `Leon4gr45/brain`, private backup repository
`JsonLord/nodepad-brain-backup:main`. Qualification dates: 2026-10-22–23 UTC.

Runtime source: `d45bd4ac1b715cae7e9cc6e3a61755750afabb6f` on
`feat/unified-auth-runtime`. Hugging Face source revision:
`3b14a4db12c95292699bd4b45689636bc73ea557`.

The exact committed tree was uploaded with `deploy/hf_space.py`, with all 721
tracked files verified. Stale files from the previous partial deployment were
removed; only HF-managed `.gitattributes` remains outside the Git tree. Both
required Ollama scripts are present. The initial HF image build and the subsequent
factory rebuild both passed.

## First boot and publication

After the operator corrected Space secret configuration, canonical `SYSTEM_PAT`
authentication and the backup readiness gate passed. PostgreSQL/Redis readiness
preceded backup validation, workspace bootstrap, Supervisor and the API/MCP/worker.
The Space's own `GITHUB_PAT` published an initial empty checkpoint:
`2487f2c58c4e5588f3509127c62a0c5ec3d12169`.

Only disposable workspace `backup-live-test` was created. Normal Agent memory
ingestion completed; Agent search and context returned its unique marker.
The normal 300-second checkpoint published:
`e1a68f20e34882f4cdf0ec77d8b075a9db06d67e`.

Remote validation passed for manifest, payload checksums, PostgreSQL logical
archive, source SHA, `embeddinggemma2-text-v1`, and stored dimension 512. The
logical SQL contains the unique fact. Registry workspace and canonical Brain IDs
match the live originals. Credential signatures and known locally available
credentials were absent from the registry/archive; auth/session tables were absent.
Runtime-only credential values were never read back: their exclusion also relies
on the server's pre-publication secret scan and content-table allowlist.

## Rebuild and restoration

The factory rebuild passed. New PostgreSQL cluster initialization was visible in
logs, the container startup ID changed, and `/health` reported `restored=true`
with exact checkpoint `e1a68f20e34882f4cdf0ec77d8b075a9db06d67e`. Restore validation
finished before workspace bootstrap and before API/MCP/worker startup. Canonical
workspace and Brain IDs were preserved; Agent search/context returned the original
fact from the fresh container.

A second unique fact completed normal ingestion and was retrievable. A normal
restart exercised graceful final checkpoint publication, producing a fast-forward
commit `11cee4e7d6c62babb6589a76fb93a474aa41dfdb`. Both facts were verified inside
its checksum-validated logical dump and the credential scan passed again.

That restart exposed an HF replacement overlap: the replacement restored the prior
checkpoint before the stopping container finished its final push. The second fact
was safe in GitHub but temporarily absent from the replacement. A further restart
against the now-stable remote checkpoint restored both facts with the same IDs.
No remote history was rewritten or stale state force-pushed. Planned maintenance must wait for a completed
checkpoint before starting replacement containers; a shutdown checkpoint alone is
not a barrier for HF's overlapping replacement startup.

Latest-checkpoint retrieval passed: both facts returned through Agent search and
context after restoring `11cee4e7d6c62babb6589a76fb93a474aa41dfdb`. An unchanged
reconciliation at `2026-10-23T09:46:27.185060+00:00` reported ready/clean, with the
same remote commit and content digest; no redundant checkpoint was committed.

Only the disposable Brain was deleted through the normal lifecycle API, and its
workspace was archived. The normal periodic checkpoint recorded deletion as
`eef37a45d3d1ee7f1776d363d1b42d60e4b3eb15`. Its validated registry contains no
Brains/workspaces, no Brain archive remains in the latest tree, live registered
Brain count is zero, and all earlier commits remain ancestors of the deletion
commit. No legitimate workspace or repository history was deleted.

Auth/registry regressions passed before and after restoration: SYSTEM_PAT HTTP,
console session/CSRF logout and MCP; unknown Brain reads return side-effect-free 404; duplicate create
returns 409; malformed structured ingestion returns 422 without a task ID or
invalid task polling.

No queue-full trace storm was found in captured logs (47,987 captured events on
2026-10-23; zero queue-full signatures). A separate Uvicorn access
log formatting defect was observed: log redaction clears the positional arguments
that Uvicorn's access formatter expects, causing `ValueError: not enough values
to unpack (expected 5, got 0)`. This does not establish a backup failure.

Persistence remains explicitly ephemeral and health reports degraded with
`production_ready=false`. Asynchronous checkpoint RPO is approximately 300 seconds
plus snapshot/upload time; uncheckpointed writes may be lost on abrupt termination.
The actual GitHub push → fresh container rebuild → GitHub restore → retrieval
round-trip passed. **Production NO-GO** remains appropriate for this deployment:
ephemeral storage explicitly reports `production_ready=false`, replacement overlap
requires a completed pre-maintenance checkpoint, and the access-log formatting
defect remains unresolved. No runtime architecture or auth/embedding contracts were
changed during this qualification; the follow-up changes are documentation only.
