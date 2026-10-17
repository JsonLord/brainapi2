# syntax=docker/dockerfile:1.4

# Pin official release checksums and extract only the CPU runtime. Importing the
# GPU image duplicates gigabytes under VFS before the runtime COPY can filter it.
FROM debian:bookworm-slim AS ollama-source
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl zstd \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=proxy_ca \
    set -eu; \
    if [ -f /run/secrets/proxy_ca ]; then export CURL_CA_BUNDLE=/run/secrets/proxy_ca; fi; \
    case "$TARGETARCH" in \
        amd64) ollama_sha=c164ab8b1b28e2a247c29719b8e404c423c5b83ae64c3ab4e59d50c7ff7fc5e0 ;; \
        arm64) ollama_sha=0a279d9428ad17b06fdb7dddf3b059b2c148433585fa3b30676a102fc459bb9f ;; \
        *) echo "Unsupported Ollama CPU architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl --fail --location --retry 3 \
        "https://github.com/ollama/ollama/releases/download/v0.40.6/ollama-linux-${TARGETARCH}.tar.zst" \
        --output /tmp/ollama.tar.zst; \
    echo "$ollama_sha  /tmp/ollama.tar.zst" | sha256sum --check --strict; \
    tar --zstd --extract --file /tmp/ollama.tar.zst --directory /usr \
        --wildcards --no-wildcards-match-slash --no-recursion 'bin/ollama' 'lib/ollama/*'; \
    rm /tmp/ollama.tar.zst

FROM debian:bookworm-slim AS ollama-models
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl libstdc++6 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ollama-source /bin/ollama /bin/ollama
COPY --from=ollama-source /usr/lib/ollama/*.so* /usr/lib/ollama/
COPY --from=ollama-source /usr/lib/ollama/llama-server /usr/lib/ollama/llama-server
COPY --from=ollama-source /usr/lib/ollama/*LICENSE* /usr/lib/ollama/
ARG OLLAMA_EMBEDDING_MODEL=embeddinggemma-2:270m
ARG OLLAMA_CHAT_MODEL=
ENV OLLAMA_MODELS=/opt/ollama-models OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NO_CLOUD=true
COPY scripts/preload_ollama_models.sh /tmp/preload_ollama_models.sh
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    OLLAMA_EMBEDDING_MODEL="$OLLAMA_EMBEDDING_MODEL" OLLAMA_CHAT_MODEL="$OLLAMA_CHAT_MODEL" \
    bash /tmp/preload_ollama_models.sh

# Assemble CPU/model files once, avoiding repeated large runtime snapshots on VFS.
FROM scratch AS ollama-runtime
COPY --from=ollama-models /bin/ollama /usr/bin/ollama
COPY --from=ollama-models /usr/lib/ollama /usr/lib/ollama
COPY --from=ollama-models /opt/ollama-models /opt/ollama-models

# ── Stage 1: Console builder ────────────────────────────────
FROM node:22.22.0-bookworm-slim AS console-builder

WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm ci
COPY console/ ./
RUN chmod +x node_modules/.bin/* 2>/dev/null || true
RUN npm run build

# ── Stage 2: Python builder ─────────────────────────────────
FROM python:3.11.14-slim-bookworm AS builder

WORKDIR /app

ARG INSTALL_LOCAL_ML=false

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_RETRIES=8 \
    PIP_TIMEOUT=900 \
    PIP_DEFAULT_TIMEOUT=900 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    HF_HOME=/app/.cache \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache \
    TIKTOKEN_CACHE_DIR=/app/.cache/tiktoken

RUN --mount=type=secret,id=proxy_ca \
    set -eu; \
    if [ -f /run/secrets/proxy_ca ]; then \
        export PIP_CERT=/run/secrets/proxy_ca GIT_SSL_CAINFO=/run/secrets/proxy_ca; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
        build-essential git postgresql-15 postgresql-server-dev-15 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir poetry==2.1.3 \
    && git clone --branch v0.5.1 https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector && make && make install \
    && rm -rf /tmp/pgvector /root/.cache

COPY pyproject.toml poetry.lock ./

RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
        export SSL_CERT_FILE=/run/secrets/proxy_ca PIP_CERT=/run/secrets/proxy_ca REQUESTS_CA_BUNDLE=/run/secrets/proxy_ca; \
    fi; \
    if [ "$INSTALL_LOCAL_ML" = "true" ]; then \
        poetry sync --no-root --only main --with local-ml; \
    else \
        poetry sync --no-root --only main; \
    fi \
    && mkdir -p "$TIKTOKEN_CACHE_DIR" \
    && /app/.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" \
    && rm -rf /root/.cache /tmp/*

COPY src/ ./src/
COPY scripts/preload_docker_models.py ./scripts/

RUN if [ "$INSTALL_LOCAL_ML" = "true" ]; then \
        /app/.venv/bin/python scripts/preload_docker_models.py; \
    fi \
    && rm -rf /root/.cache /tmp/*

# Validate small runtime inputs before combining them with large dependencies.
FROM debian:bookworm-slim AS runtime-config
WORKDIR /runtime/app
COPY deploy/ ./deploy/
COPY entrypoint.sh ./
COPY scripts/ ./scripts/
RUN test -f scripts/start_with_ollama.py \
    && test -f scripts/preload_ollama_models.sh \
    && chmod +x entrypoint.sh

# Assemble files on an empty root, avoiding a full PostgreSQL/Python root copy
# for every small COPY when the cloud Docker daemon uses VFS snapshots.
FROM scratch AS runtime-files
COPY --from=builder /usr/lib/postgresql/15/lib/vector.so /usr/lib/postgresql/15/lib/
COPY --from=builder /usr/share/postgresql/15/extension/vector* /usr/share/postgresql/15/extension/
COPY --from=runtime-config /runtime/ /
COPY --from=console-builder /console/dist /app/console/dist
COPY --from=builder /app /app
COPY --from=ollama-runtime / /

# ── Stage 3: runtime ────────────────────────────────────────
FROM python:3.11.14-slim-bookworm

WORKDIR /app

ARG BUILD_DATE
ARG BUILD_SHA

LABEL build_date="${BUILD_DATE}" \
      build_sha="${BUILD_SHA}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/app/.cache \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache \
    TIKTOKEN_CACHE_DIR=/app/.cache/tiktoken \
    PIP_NO_CACHE_DIR=1 \
    PIP_RETRIES=8 \
    PIP_TIMEOUT=900 \
    PIP_DEFAULT_TIMEOUT=900

RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    curl \
    util-linux \
    postgresql-15 \
    postgresql-contrib-15 \
    redis-server \
    nginx \
    supervisor \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y setuptools wheel \
    && groupadd -r appuser && useradd -r -g appuser -m appuser \
    && mkdir -p /app/plugins && chown appuser:appuser /app/plugins

COPY --from=runtime-files / /
VOLUME ["/app/plugins"]

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=30s --start-period=120s --retries=5 \
    CMD curl -f http://127.0.0.1:7860/health || exit 1

USER root
ENTRYPOINT ["/app/entrypoint.sh"]
