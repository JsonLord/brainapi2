# BrainAPI deploy profiles

Choose **heavy** or **light** when bringing up a new deployment.

| | Heavy | Light |
| --- | --- | --- |
| Compose | [`docker-compose.heavy.yaml`](docker-compose.heavy.yaml) | [`docker-compose.light.yaml`](docker-compose.light.yaml) |
| Env template | [`env.heavy.example`](env.heavy.example) | [`env.light.example`](env.light.example) |
| Graph | Neo4j | NetworkX on Postgres |
| Data | MongoDB | PostgreSQL |
| Vectors | Milvus | PostgreSQL + pgvector |
| LLMs | GCP Vertex (small) + Azure (large) | DeepSeek (small + large) |
| Embeddings | Azure | OpenAI (`text-embedding-3-large`) |
| Container limits | Full (current production) | Smaller footprint (`mem_limit` / `cpus`) |
| Suggested VM | ~8–16 GB RAM / 4+ vCPU | ~2 GB RAM / 1 vCPU |

## Quick start

```bash
# 1. Copy a profile template outside the repository and replace every secret.
install -m 600 deploy/env.light.example /etc/brainapi/light.env
# Set BRAINAPI_ENV_FILE to the absolute file path in /etc/brainapi/light.env.

# 2. Validate first, then start and wait for health.
docker compose --env-file /etc/brainapi/light.env \
  -f deploy/docker-compose.light.yaml config -q
docker compose --env-file /etc/brainapi/light.env \
  -f deploy/docker-compose.light.yaml up -d --wait
# or
docker compose --env-file /etc/brainapi/heavy.env \
  -f deploy/docker-compose.heavy.yaml up -d --wait
```

Only nginx publishes host ports (`80` and `443`). The API, embedded Console,
and MCP are reached through `/`, `/console/`, and `/mcp`. The checked-in TLS
server rejects handshakes until an operator mounts a certificate-specific
nginx server file; do not expose port 80 beyond a trusted proxy without HTTPS.

## Light sizing

| Service | Heavy | Light |
| --- | --- | --- |
| nginx | 128m / 0.2 CPU | 64m / 0.1 CPU |
| redis | 2048m / 0.5 CPU (`maxmemory 1500mb`) | 256m / 0.1 CPU (`maxmemory 192mb`) |
| postgres | — (not in heavy compose) | 768m / 0.35 CPU |
| neo4j / milvus / mongo / etcd / minio | present | omitted |
| api / worker / mcp | 2–4g / 1–2 CPU | 768m / 0.5 CPU each |
| celery `--concurrency` | 2 | 1 |
| `CELERY_WORKER_CONCURRENCY` | 4 | 1 |

## Notes

- `BRAINAPI_IMAGE`, `BRAINAPI_ENV_FILE`, Redis credentials, and profile backend
  credentials are required and fail during Compose interpolation when absent.
- Mount cloud credentials with a private operator-owned Compose override; no
  host-specific credential path is present in the production profiles.
- Do not mix profiles under the same Compose project name. Their volume layouts
  and restore order differ.

## Backup and restore

Backups default to `/srv/brainapi/backups` and are created with mode `0700`.
They contain sensitive application data (including stored brain credentials),
so encrypt and access-control the backup directory. The generated manifest
contains only profile/version metadata, service names, restore order, and
checksums; it never contains secrets.

```bash
deploy/brainapi-backup backup --profile light \
  --env-file /etc/brainapi/light.env --project-name brainapi
deploy/brainapi-backup verify --profile light \
  --env-file /etc/brainapi/light.env --archive /srv/brainapi/backups/light-UTCSTAMP
deploy/brainapi-backup restore --profile light \
  --env-file /etc/brainapi/light.env --archive /srv/brainapi/backups/light-UTCSTAMP
```

Backup stops nginx, API, MCP, and workers before capture. Restore refuses a
running stack, a profile/image mismatch, a checksum mismatch, or any non-empty
target volume. Use a documented maintenance window and retain the generated
manifest with the release artifacts. Light backups include the system registry
plus an individual custom-format dump for every `brain_*` PostgreSQL database.

## Production validation

The CI-only Compose override supplies a deterministic OpenAI-compatible model
stub; it is not a production deployment file. It lets both profiles exercise
the complete storage and gateway path without external model credentials:

```bash
docker compose -f deploy/docker-compose.light.yaml \
  -f deploy/docker-compose.ci.yaml up -d --wait
python scripts/production_smoke.py exercise --profile light \
  --system-token "$BRAINPAT_TOKEN" --artifact-dir release-artifacts
```

After backup, clean-volume restore, and restart, run the same tool with
`verify-restore`. CI retains the profile smoke, latency, restore, backup
manifest, image audit, and SBOM evidence. The tag workflow downloads the exact
commit's successful light and heavy artifacts and runs the release-readiness
gate before any image can be published. Quality CI exports one `linux/amd64`
candidate archive; the heavy runner loads that same archive, and the tag
workflow publishes it unchanged after verifying its recorded image ID.

## Public documentation sandbox

The anonymous sandbox is an opt-in extension of the light profile. Merge
[`env.public-demo.example`](env.public-demo.example) into the deployment's
private environment, deploy the current API and worker image, and seed only the
dedicated `agentdemo` brain:

```bash
docker compose --env-file /etc/brainapi/light.env -f deploy/docker-compose.light.yaml up -d
docker compose --env-file /etc/brainapi/light.env -f deploy/docker-compose.light.yaml exec brainapi \
  python scripts/seed_public_demo.py --api-url http://localhost:8000
```

The seed command downloads the published V2 documentation, records its SHA-256
marker, skips an already-current corpus, and polls the asynchronous ingestion
task. It requires the private system `BRAINPAT_TOKEN` inside the deployment; do
not expose that token through the gateway. The seed command explicitly creates
the brain; `BRAIN_CREATION_ALLOWED` does not enable implicit API provisioning.

Expose the API origin as `api.brain-api.dev`, with TLS, and put the Cloudflare
gateway in front of root-domain `/api/*`. The application itself deliberately
does not implement client rate limiting; the checked-in gateway owns the
30-request/60-second demo limit.


## Brain registry contract

The persisted registry is authoritative. Retrieval, query, agent, and MCP access
cannot provision an unknown brain, even with `BRAIN_CREATION_ALLOWED=true`.
Explicit unknown API scopes return `404 BRAIN_NOT_FOUND`. A missing scope can
resolve to `default` only when `DEFAULT_BRAIN_FALLBACK=true` and that brain is
already registered. Create brains with `POST /system/brains` or
`POST /system/workspaces`; duplicate brain creation returns
`409 BRAIN_ALREADY_EXISTS`, including concurrent inserts.

Brain IDs accept 1–128 ASCII letters, digits, hyphens, and underscores, starting
with a letter or digit. `system` is reserved (case insensitive). The same rules
apply to create, clear, and delete; workspace slugs retain their narrower syntax.
`/system/brains-list` returns persisted registry identities only. Orphan physical
databases never appear as registered brains or acquire generated PATs.

Clear preserves registered identity and physical storage. Explicit delete also
removes the PostgreSQL database, including a named orphan without a registry
row. These are destructive operations: target only an intended identifier.

Run the deterministic gate and the opt-in real integration gate:

```bash
PYTHONPATH=.:tests python -m pytest -q tests/test_brain_registry_contract.py tests/test_brain_lifecycle.py
BRAINAPI_REGISTRY_TEST_CONTAINER=<disposable-local-container> \
  PYTHONPATH=.:tests python -m pytest -q tests/integration/test_brain_registry_contract.py
```

The integration gate requires a disposable local HF CPU container with the
candidate source and its normal entrypoint environment. It invokes
`bash /app/entrypoint.sh --qualify-registry`, compares actual PostgreSQL catalogs
and application Redis state around unknown GET/search/context requests, tests
concurrent storage inserts, and deletes only its own collision-checked temporary
namespaces. It does not clean production probe artifacts or other existing brains.
