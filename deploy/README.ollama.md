# Hugging Face CPU retrieval profile

The default Space runs PostgreSQL/pgvector, NetworkX, Redis, API, MCP, Celery,
nginx, and local Ollama. nginx is public on 7860; the API stays internal on 8000
and Ollama on 127.0.0.1:11434. Persistent state requires the Space's `/data` volume.

## Qualified embedding contract

The default is **`embeddinggemma-2:270m`**, the text/code-only 270M model.
Google's [model card](https://huggingface.co/google/embeddinggemma-2/blob/914f7f89142e33e77833254d9c9b90c3cef7303b/README.md)
is authoritative: 8192 input tokens, 768 native dimensions, and MRL sizes
512/256/128. The registry's larger context metadata must not expand this limit.
The adapter sets `num_ctx=8192` and `truncate=false`; overlong inputs fail rather
than silently losing text. The instruction prefix consumes part of that budget.

BrainAPI's `embeddinggemma2-text-v1` profile validates every native value
(including the discarded tail) as numeric and finite, requires exactly 768
values, retains the first 512, then L2-normalizes the retained vector. Zero-norm
or invalid vectors fail. Both stored documents and retrieval queries use this
same transformation. All five primary vector tables use **`vector(512)`**.
Existing per-store `EMBEDDING_*_DIMENSION` variables remain authoritative; there
is no second stored-dimension setting. See [env.hf.cpu.example](env.hf.cpu.example).

Task steering is centralized in the Ollama adapter, following Google's search
instructions exactly:

- Document: `title: none | text: {original_text}` (the API supplies no separate title).
- Query: `task: search result | query: {original_query}`.

`embed_text`/`embed_texts` embed documents; `embed_query` selects query steering.
Other embedding providers inherit their existing symmetric behavior. BrainAPI
requests the full native vector from Ollama and performs MRL itself; it does not
ask Ollama to reduce the output dimension.

Each PostgreSQL store records its provider, model, profile, native/stored
sizes, task instructions, and vector transformation in `embedding_profiles`.
An incompatible or unlabelled existing index fails closed before read/write,
even when its dimensions happen to match. Dimension changes never drop vector
tables automatically. Use a new empty brain/index, or explicitly rebuild an
index by re-embedding its original source texts. This is a pre-production schema
initialization, with no migration or automatic reuse of Qwen vectors.

## Build and startup

Ollama **0.40.6** is pinned by official release SHA-256 checksums; 0.40.1 cannot load the
`gemma-embedding2` architecture. Only CPU runtime files are extracted from the
verified release, avoiding GPU source-image duplication under VFS. The default
image preloads only the Gemma model.
`OLLAMA_CHAT_MODEL` is an empty optional build argument. No Qwen chat model or
reranker is required by lightweight ingestion, ordinary `/retrieve/search`, or
one-shot `/retrieve/context`. Accurate graph enrichment, generative routes, and
deep navigation require separately configured chat models/providers.

Ollama allows one inference request and one resident model, uses two CPU threads,
and keeps embeddings warm for ten minutes. Restarts reuse `/opt/ollama-models`;
there is no runtime pull or download fallback. API/MCP/worker startup waits for
the **exact configured embedding tag** and exits if it is missing. Runtime model
changes require rebuilding the image with the matching model and configuration.

Qwen embeddings remain a generic alternative for other deployments: explicitly
build with `OLLAMA_EMBEDDING_MODEL=qwen3-embedding:0.6b`, configure that same runtime
model, use a different profile identifier, unset MRL, set its native and all five
stored dimensions to 1024, and use a fresh index. The HF CPU profile never
preloads Qwen by default.

## Qualification before production

Commit the intended source first, then stage and build that exact revision:

```sh
python deploy/hf_space.py stage --revision HEAD --output /tmp/brainapi-hf-source
cd /tmp/brainapi-hf-source
docker build --no-cache -t brainapi-hf-test .
```

Use an empty output directory; retain the adjacent source checksum manifest.
In managed cloud builds, supply the session proxy and CA to networked build
steps using BuildKit secrets; never disable TLS checks or bake a session CA into
the production image. An environment failure is not a successful image build.

Run the image with a development `BRAINPAT_TOKEN`, then execute
`python scripts/qualify_hf_embeddings.py` **inside that image**. It checks the
baked model list, raw 768d and transformed 512d finite embeddings, all five fresh
schema dimensions, real asynchronous workspace ingestion, persisted vectors,
POST search/context retrieval, and isolation between two workspaces. Failures
are errors, never successful skips. It creates uniquely named qualification
workspaces; run it on a local pre-production instance.

Run focused tests with `python -m pytest -q tests/test_embeddinggemma2.py
 tests/test_ollama_embeddings.py tests/test_ollama_startup.py`. Unit tests alone
do not establish model quality or production readiness. Do not deploy until the
staged Docker build and real integration qualification pass.

## Optional cross-encoder reranking

Reranking stays disabled unless a request selects a configured plugin. A separate
compatible model/server can expose `LLAMA_RERANK_URL=http://127.0.0.1:11435` and
`rerank=plugin:llama_cpp`; this image does not download, preload, or start it.
Qualify Gemma -> pgvector/BM25 -> fusion independently before adding reranking.
