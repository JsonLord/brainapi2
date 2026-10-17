import io
import json

import pytest

from scripts import start_with_ollama


@pytest.mark.parametrize('names,ready', [
    (['embeddinggemma-2:270m'], True),
    (['embeddinggemma-2:latest'], False),
    (['qwen3-embedding:0.6b'], False),
    ([], False),
])
def test_startup_requires_exact_baked_embedding_tag_without_pulling(monkeypatch, names, ready):
    monkeypatch.setenv('OLLAMA_EMBEDDING_MODEL', 'embeddinggemma-2:270m')
    ticks = iter([0, 0, 2])
    monkeypatch.setattr(start_with_ollama.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(start_with_ollama.time, 'sleep', lambda _: None)
    class Opener:
        def open(self, url, timeout):
            assert url.endswith('/api/tags')
            return io.BytesIO(json.dumps({'models': [{'name': n} for n in names]}).encode())
    monkeypatch.setattr(start_with_ollama.urllib.request, 'build_opener', lambda *_: Opener())
    if ready:
        start_with_ollama.wait_for_ollama(timeout=1)
    else:
        with pytest.raises(RuntimeError, match='preload it during the image build'):
            start_with_ollama.wait_for_ollama(timeout=1)
