"""llama.cpp's native cross-encoder endpoint; never use Ollama chat to rerank."""

import math
import os

import httpx


def rerank(query, candidates, k):
    if not candidates or k <= 0:
        return []
    base_url = os.environ["LLAMA_RERANK_URL"].rstrip("/")
    with httpx.Client(base_url=base_url, timeout=120, trust_env=False) as client:
        response = client.post(
            "/v1/rerank",
            json={
                "query": query,
                "documents": [item["text"] for item in candidates],
                "top_n": min(k, len(candidates)),
            },
        )
        response.raise_for_status()
        results = response.json().get("results")
    if not isinstance(results, list):
        raise ValueError("llama.cpp returned an invalid reranking response")
    ranked = []
    seen = set()
    for item in results:
        index = item.get("index")
        score = item.get("relevance_score")
        if (
            type(index) is not int or index < 0 or index >= len(candidates)
            or index in seen or isinstance(score, bool)
            or not isinstance(score, (int, float)) or not math.isfinite(score)
        ):
            raise ValueError("llama.cpp returned an invalid reranking result")
        seen.add(index)
        ranked.append({**candidates[index], "score": score})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)[:k]
