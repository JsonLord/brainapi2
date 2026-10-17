"""Identity persisted with an index; equal dimensions alone do not imply compatibility."""


class EmbeddingProfileError(ValueError):
    pass


def index_profile(config, stored_dimension: int) -> dict:
    provider = config.embeddings_provider
    attribute = {"gcp_vertex": "gcp", "amazon_bedrock": "bedrock"}.get(provider, provider)
    provider_config = getattr(config, attribute, None)
    model = getattr(provider_config, "embedding_model", None) or config.embeddings.local_model
    gemma = provider == "ollama" and model == "embeddinggemma-2:270m"
    return {
        "profile": config.embeddings.profile or f"{provider}:{model}",
        "provider": provider,
        "model": model,
        "native_dimension": config.embeddings.native_dimension if gemma else stored_dimension,
        "stored_dimension": stored_dimension,
        "transform": "truncate-512-l2" if gemma else "identity",
        "instructions": "google-search-query-document-v1" if gemma else "none",
    }


def require_matching_profile(existing: dict | None, expected: dict, *, legacy_index: bool = False) -> None:
    if (existing is None and legacy_index) or (existing is not None and existing != expected):
        raise EmbeddingProfileError(
            f"Embedding index/profile mismatch: existing={existing!r}, configured={expected!r}. "
            "Use a new empty brain/index or explicitly re-index its source texts; "
            "existing vectors will not be reused or deleted automatically."
        )
