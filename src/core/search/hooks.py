from __future__ import annotations

from typing import Any, Callable, Optional
import os

from src.core.search.graph_channels import CORE_CHANNELS

RERANK_MAX_K = 10
CATALOG_RERANK_MAX_K = 50
CATALOG_RETRIEVE_MIN_K = 50
CATALOG_RETRIEVE_MAX_K = 200


def retrieve_k_for_mode(mode: str | None, k: int) -> int:
    if (mode or "default") == "catalog":
        return min(CATALOG_RETRIEVE_MAX_K, max(int(k), CATALOG_RETRIEVE_MIN_K))
    return int(k)


def rerank_max_k_for_mode(mode: str | None) -> int:
    if (mode or "default") == "catalog":
        return CATALOG_RERANK_MAX_K
    return RERANK_MAX_K


RetrieveFn = Callable[
    [str, str, int],
    tuple[list[str], dict[str, float], dict[str, str]],
]
RerankFn = Callable[[str, list[dict[str, Any]], int], list[dict[str, Any]]]

_retrievers: dict[str, RetrieveFn] = {}
_rerankers: dict[str, RerankFn] = {}


class SearchPluginError(ValueError):
    pass


def reset_search_plugins() -> None:
    _retrievers.clear()
    _rerankers.clear()


def register_search_retriever(name: str, fn: RetrieveFn) -> None:
    key = _clean_name(name)
    if not key:
        raise SearchPluginError("Retriever name is required.")
    _retrievers[key] = fn


def register_search_reranker(name: str, fn: RerankFn) -> None:
    key = _clean_name(name)
    if not key:
        raise SearchPluginError("Reranker name is required.")
    _rerankers[key] = fn


def get_search_retriever(name: str) -> Optional[RetrieveFn]:
    return _retrievers.get(_clean_name(name))


def get_search_reranker(name: str) -> Optional[RerankFn]:
    if _clean_name(name) == "llama_cpp" and os.getenv("LLAMA_RERANK_URL"):
        from src.lib.reranking.client_llama_cpp import rerank
        return rerank
    return _rerankers.get(_clean_name(name))


def listed_retrievers() -> list[str]:
    return sorted(_retrievers)


def listed_rerankers() -> list[str]:
    names = set(_rerankers)
    if os.getenv("LLAMA_RERANK_URL"):
        names.add("llama_cpp")
    return sorted(names)


def _clean_name(name: str) -> str:
    return str(name or "").strip().lower()


def parse_rerank(value: Optional[str]) -> Optional[str]:
    raw = (value or "").strip()
    if not raw or raw.lower() == "none":
        return None
    lowered = raw.lower()
    if lowered == "linear":
        raise SearchPluginError(
            "rerank=linear is not implemented. Use none or plugin:<name>."
        )
    if not lowered.startswith("plugin:"):
        raise SearchPluginError(
            f"Invalid rerank={raw!r}. Expected none or plugin:<name>."
        )
    name = _clean_name(raw.split(":", 1)[1])
    if not name:
        raise SearchPluginError("rerank=plugin: requires a plugin name.")
    return name


def parse_plugin_channels(channels: Optional[list[str]]) -> list[str]:
    names: list[str] = []
    for item in channels or []:
        raw = str(item or "").strip()
        if not raw:
            continue
        lowered = raw.lower()
        if lowered in CORE_CHANNELS:
            continue
        if lowered.startswith("plugin:"):
            name = _clean_name(raw.split(":", 1)[1])
            if not name:
                raise SearchPluginError("channels plugin: requires a plugin name.")
            names.append(name)
            continue
        if get_search_retriever(lowered) is not None:
            names.append(lowered)
            continue
        raise SearchPluginError(f"Unknown search channel {raw!r}.")
    return names


def resolve_reranker(value: Optional[str]) -> Optional[RerankFn]:
    name = parse_rerank(value)
    if name is None:
        return None
    fn = get_search_reranker(name)
    if fn is None:
        available = ", ".join(listed_rerankers()) or "(none loaded)"
        raise SearchPluginError(
            f"Unknown search rerank plugin {name!r}. Loaded: {available}."
        )
    return fn


def resolve_retrievers(channels: Optional[list[str]]) -> list[tuple[str, RetrieveFn]]:
    resolved: list[tuple[str, RetrieveFn]] = []
    for name in parse_plugin_channels(channels):
        fn = get_search_retriever(name)
        if fn is None:
            available = ", ".join(listed_retrievers()) or "(none loaded)"
            raise SearchPluginError(
                f"Unknown search retriever plugin {name!r}. Loaded: {available}."
            )
        resolved.append((name, fn))
    return resolved
