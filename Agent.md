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
- `Dockerfile` (exposing port `7860` and uvicorn running on port `7860`)
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
   hf upload Leon4gr45/brain --repo-type=space
   ```

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
- Method: POST
- Purpose: Retrieve relevant information for a piece of text (graph triples, passages).
- Request:
  {
    "text": "hello world",
    "brain_id": "default"
  }
- Response:
  {
    "text_context": "..."
  }

### /retrieve/search
- Method: GET
- Purpose: Ranked search hits.
- Request: /retrieve/search?query=hello
- Response:
  {
    "results": [...]
  }
