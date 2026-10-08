"""Wait for the baked local embedding model before starting a service."""

import json
import os
import sys
import time
import urllib.request


def wait_for_ollama(timeout=120):
    host = os.environ.get("OLLAMA_HOST", "127.0.0.1")
    port = os.environ.get("OLLAMA_PORT", "11434")
    model = os.environ.get("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(f"http://{host}:{port}/api/tags", timeout=3) as response:
                models = json.load(response).get("models", [])
            if any(item.get("name") == model for item in models):
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError(f"Ollama embedding model {model!r} is unavailable; preload it during the image build")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("A service command is required")
    if os.environ.get("EMBEDDINGS_PROVIDER", "ollama") == "ollama":
        wait_for_ollama()
    os.execvp(sys.argv[1], sys.argv[1:])
