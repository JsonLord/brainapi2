#!/usr/bin/env bash
set -euo pipefail
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_MODELS=${OLLAMA_MODELS:-/opt/ollama-models}
ollama serve > /tmp/ollama-preload.log 2>&1 &
ollama_pid=$!
trap 'kill "$ollama_pid" 2>/dev/null || true; wait "$ollama_pid" 2>/dev/null || true' EXIT
ready=false
for attempt in {1..60}; do
    if curl --noproxy '*' --fail --silent http://127.0.0.1:11434/api/version >/dev/null; then
        ready=true
        break
    fi
    if ! kill -0 "$ollama_pid" 2>/dev/null; then
        cat /tmp/ollama-preload.log >&2
        exit 1
    fi
    sleep 1
done
if [ "$ready" != true ]; then
    echo 'Ollama did not become ready during model preload' >&2
    exit 1
fi
ollama pull "${OLLAMA_EMBEDDING_MODEL:-qwen3-embedding:0.6b}"
if [ -n "${OLLAMA_CHAT_MODEL-qwen3:0.6b}" ]; then
    ollama pull "${OLLAMA_CHAT_MODEL-qwen3:0.6b}"
fi
