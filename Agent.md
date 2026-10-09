# Agent Deployment Guide for Hugging Face Space

This document serves as the guide and best practices for automated agents deploying and maintaining BrainAPI on Hugging Face Spaces.

## 1. Deployment Configuration

### Target Space
- **Profile:** `Leon4gr45`
- **Space:** `brain`
- **Full Identifier:** `Leon4gr45/brain`
- **Frontend Port:** `7860` (mandatory for Hugging Face Spaces)

### Deployment Method
- **Docker SDK** (`Dockerfile`)

### HF Token
- Always pass/read the HF access token from the environment (`HF_TOKEN` environment variable). Never hardcode secrets or token strings in repository files.

### Required Files
- `Dockerfile` (exposing nginx on port `7860`; uvicorn stays internal on port `8000`)
- `README.md` with Hugging Face YAML frontmatter:
  ```yaml
  ---
  title: BrainAPI
  sdk: docker
  app_port: 7860
  ---
  ```
- `.hfignore` to exclude `.git`, `.venv`, `node_modules`, and cache directories
- `Agent.md` (this file)

---

## 2. API Exposure and Documentation

### Mandatory Endpoints

- **`/health`**
  - Method: GET
  - Purpose: Returns HTTP 200 when the app is ready. Required for Hugging Face Space liveness.
  - Request: None
  - Response:
    ```json
    {
      "status": "ok",
      "service": "brainapi",
      "version": "2.19.0-dev"
    }
    ```

- **`/api-docs`**
  - Method: GET
  - Purpose: Redirects/Exposes full OpenAPI / Swagger documentation for all endpoints. Reachable at `https://Leon4gr45-brain.hf.space/api-docs`.
  - Response: HTTP 307 redirect to `/docs` or Swagger UI HTML page.

- **`/`**
  - Method: GET
  - Purpose: Redirects public root visits to `/console/`.
  - Response: HTTP 307 redirect to `/console/`.

---

## 3. Deployment Workflow & Ongoing Best Practices

1. **Pre-flight Check:**
   Clean up non-project or stale files in the target Hugging Face Space using `huggingface_hub` Python API.

2. **Deploy Command:**
   ```bash
   python deploy/hf_space.py stage --output /tmp/brainapi-hf-source
   # Inspect/build the staged tree before uploading that same Git revision.
   python deploy/hf_space.py upload --revision HEAD
   ```

   This stages the complete committed Git tree, checks every required Docker
   input, and verifies the uploaded Space files against the staged content.
   Run it from the BrainAPI checkout with `huggingface_hub` installed and
   `HF_TOKEN` set through secure environment settings. Do not upload a manually
   selected list of changed files. The readiness wrapper must remain in the
   Space repository alongside `scripts/preload_ollama_models.sh`.

3. **Log Monitoring:**
   - **Build Logs (SSE):**
     ```bash
     curl -N -H "Authorization: Bearer $HF_TOKEN" "https://huggingface.co/api/spaces/Leon4gr45/brain/logs/build"
     ```
   - **Run Logs (SSE):**
     ```bash
     curl -N -H "Authorization: Bearer $HF_TOKEN" "https://huggingface.co/api/spaces/Leon4gr45/brain/logs/run"
     ```

4. **Iterative Debugging:**
   Inspect build and run logs for missing dependencies, port binding issues, or startup exceptions. Modify codebase accordingly, redeploy, and monitor until the Space reaches `RUNNING` status and responds to `/health` and `/api-docs`.

### Functional Endpoints

### /retrieve/context
- **Method:** POST
- **Purpose:** Retrieve an entity's contextual information based on a given text, generating triples and passages.
- **Authentication:** Requires `BrainPAT` header or Bearer token unless using the system fallback.
- **Request (JSON):**
  - `text` (str, required): The text to search context for.
  - `brain_id` (str, optional, default: `"default"`): The brain/workspace identifier to query.
  - `historical_limit` (int, optional, default: `10`)
  - `max_facts` (int, optional, default: `40`, min: `0`)
  - `max_passages` (int, optional, default: `8`)
  - `apply_fact_filter` (bool, optional, default: `true`)
  - `use_ppr` (bool, optional, default: `true`)
  - `sufficiency_retry` (bool, optional, default: `false`)
  - `profile_stages` (bool, optional, default: `false`)
  - `cross_event_bridges` (int, optional, default: `3`, min: `0`)
  ```json
  {
    "text": "Identify memory leaks in Python",
    "brain_id": "default",
    "historical_limit": 10,
    "max_facts": 40,
    "max_passages": 8,
    "apply_fact_filter": true,
    "use_ppr": true,
    "sufficiency_retry": false,
    "profile_stages": false,
    "cross_event_bridges": 3
  }
  ```
- **Response (JSON):**
  ```json
  {
    "text_context": "Found 3 passages and 10 triples related to the query...",
    "triples": [
      {
        "identified_entity": "memory_leak",
        "triple": [
          {"id": "python", "label": "Language", "properties": {"name": "Python"}},
          {"id": "causes", "type": "CAUSES", "properties": {}},
          {"id": "memory_leak", "label": "Issue", "properties": {"name": "Memory Leak"}}
        ],
        "source_chunk_ids": ["chunk-123"]
      }
    ],
    "historical_context": ["Previous discussions about memory profiling..."],
    "source_passages": ["Python's garbage collector sometimes misses reference cycles..."],
    "graph_session_ids": ["sess-456"],
    "temporal_conflicts": [],
    "paths": [],
    "topics": [{"topic": "performance", "weight": 0.8}],
    "stage_timings": {"retrieval": 0.15, "formatting": 0.02}
  }
  ```

### /retrieve/search
- **Method:** POST (Canonical) / GET (Compatibility)
- **Purpose:** Execute a ranked search across passages, entities, events, communities, and plugins with optional re-ranking.
- **Authentication:** Requires `BrainPAT` header or Bearer token unless using the system fallback.
- **Request (POST JSON or GET Query Params):**
  - `query` (str, required): The search query.
  - `brain_id` (str, optional, default: `"default"`): The brain identifier to query.
  - `k` (int, optional, default: `10`, range: `1-200`): Number of hits to return.
  - `channels` (List[str], optional, default: `["passages"]`): Channels to search. Allowed channels include `passages`, `entities`, `events`, `communities`, and/or `plugin:<name>`.
  - `node_labels` (List[str], optional, default: `null`): Node labels to filter the entities channel.
  - `community_labels` (List[str], optional, default: `null`): Hub labels for the communities channel.
  - `expand` (str, optional, default: `"none"`): Allowed values: `"none"`, `"neighbors"`. Optional 1-hop expansion from graph channel seeds.
  - `fusion` (str, optional, default: `null`): Fusion override. Allowed values: `"rrf"`, `"cc"`.
  - `fusion_alpha` (float, optional, default: `null`, range: `0.0-1.0`)
  - `rerank` (str, optional, default: `null`): `none` or `plugin:<name>`. Unknown plugin names return 400.
  - `mode` (str, optional, default: `"default"`): Allowed values: `"default"`, `"catalog"`.
  - `profile_stages` (bool, optional, default: `false`)
  - `extras` (Dict[str, str], optional, default: `null`)
  - `target` (str, optional, default: `null`): Optional USER uuid or id for query-gated rerank of retrieved hits.
  ```json
  {
    "query": "hello world",
    "brain_id": "default",
    "k": 10,
    "channels": ["passages", "entities"],
    "node_labels": ["Document", "Author"],
    "community_labels": ["Tech"],
    "expand": "none",
    "fusion": "rrf",
    "fusion_alpha": 0.5,
    "rerank": "plugin:cohere",
    "mode": "default",
    "profile_stages": false,
    "extras": {},
    "target": "user-123"
  }
  ```
- **Response (JSON):**
  ```json
  {
    "hits": [
      {
        "id": "hit-789",
        "score": 0.95,
        "content": "Hello World example in Python",
        "metadata": {"author": "Jane Doe"}
      }
    ],
    "stage_timings": {"search": 0.08, "rerank": 0.12},
    "channel_lists": {"passages": ["hit-789"]},
    "facets": {"author": {"Jane Doe": 1}},
    "node_ids": ["node-456"]
  }
  ```