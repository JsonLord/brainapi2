# Hugging Face CPU retrieval profile

The default Space image runs PostgreSQL/pgvector, NetworkX, Redis, API, MCP,
Celery, nginx, and an internal Ollama server. Only nginx port 7860 is public;
Ollama listens on 127.0.0.1:11434 and has no nginx route.

The image preloads `qwen3-embedding:0.6b` and `qwen3:0.6b` during its build.
Restarts use the baked model files at `/opt/ollama-models`, with no startup pull.
`OLLAMA_EMBEDDING_MODEL` selects embeddings independently of the chat models;
the native `/api/embed` client supports batches and rejects truncated inputs,
malformed responses, and dimension mismatches. Failures propagate instead of
producing mock vectors. The default profile uses 1024-dimensional pgvector
columns. Changing from a 384/768-dimensional model requires rebuilding the
affected vector indexes and re-embedding the corpus; do not reuse old vectors.

Model build arguments are `OLLAMA_EMBEDDING_MODEL` and `OLLAMA_CHAT_MODEL`.
An empty `OLLAMA_CHAT_MODEL` skips its preload for embedding-only deployments;
configure the LLM providers accordingly if generative features are used.
Changing a runtime model name requires baking that model into a new image.
API/MCP/worker startup waits for the embedding model to be available. A missing
model causes a visible startup failure, rather than a startup download.

For the two-core Space, Ollama allows one inference request and one resident
model at a time; embedding calls use two threads and a ten-minute keep-alive.
Celery uses two threads. Tiny Qwen3 chat is available for features that require
reasoning, but its ingestion quality and CPU latency need workload-specific
evaluation. Ordinary dense passage search does not require chat generation.
PostgreSQL data remains ephemeral without the Space's persistent `/data` volume.

## Optional cross-encoder reranking

Use a separately supplied, compatible Qwen3-Reranker-0.6B GGUF with llama.cpp:

```sh
llama-server --model /opt/reranker/model.gguf --host 127.0.0.1 --port 11435 \
  --rerank --embedding --pooling rank --threads 2
```

Set `LLAMA_RERANK_URL=http://127.0.0.1:11435` and request
`rerank=plugin:llama_cpp` on `/retrieve/search`. The connector uses the native
`/v1/rerank` endpoint and retains candidate IDs. The existing catalog search mode
can rerank up to 50 candidates; the ordinary mode limits reranking to ten.
The model/server is optional and is not downloaded or started by this profile.
Keep port 11435 internal and supervise the separately configured server. Do not
substitute chat completion scores for cross-encoder reranking. Verify the chosen
GGUF's rank pooling support and retrieval quality before enabling it.

## Validation

Check `/health`, `/console/`, authenticated `/system/workspaces`, and MCP discovery.
Then exercise `/api/embed` with the selected model and assert nonempty, finite,
1024-dimensional vectors. Store and retrieve vectors in a fresh pgvector brain;
check that a related query ranks the matching document above unrelated text.
Run `tests/test_ollama_embeddings.py` and `tests/test_llama_cpp_reranker.py` for
transport/configuration/error handling. Unit tests do not establish model quality.
Build and run the complete image before deploying it to Hugging Face.
