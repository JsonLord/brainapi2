import math
import httpx

from src.adapters.interfaces.embeddings import EmbeddingsClient
from src.config import config


class OllamaEmbeddingsClient(EmbeddingsClient):
    def __init__(self):
        self.client = httpx.Client(
            base_url=f"http://{config.ollama.host}:{config.ollama.port}",
            timeout=120,
            trust_env=False,
        )
        self.model = config.ollama.embedding_model
        self.gemma = self.model == "embeddinggemma-2:270m"

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed documents; retrieval queries use embed_query instead."""
        return self._embed(texts, query=False)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], query=True)[0]

    def _embed(self, texts: list[str], *, query: bool) -> list[list[float]]:
        if not texts:
            return []
        options = {"num_thread": config.ollama.num_threads}
        if self.gemma:
            # Google's 8192-token contract overrides the registry's context metadata.
            options["num_ctx"] = 8192
            prefix = "task: search result | query: " if query else "title: none | text: "
            texts = [prefix + text for text in texts]
        response = self.client.post(
            "/api/embed",
            json={
                "model": self.model,
                "input": texts,
                "truncate": False,
                "keep_alive": config.ollama.embedding_keep_alive,
                "options": options,
            },
        )
        response.raise_for_status()
        embeddings = response.json().get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ValueError("Ollama returned an incorrect number of embeddings")
        dimensions = set()
        for vector in embeddings:
            if not isinstance(vector, list) or not vector or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in vector
            ):
                raise ValueError("Ollama returned an invalid embedding vector")
            dimensions.add(len(vector))
        if len(dimensions) != 1:
            raise ValueError("Ollama returned inconsistent embedding dimensions")
        expected = {
            config.embeddings.embedding_nodes_dimension,
            config.embeddings.embedding_triplets_dimension,
            config.embeddings.embedding_observations_dimension,
            config.embeddings.embedding_data_dimension,
            config.embeddings.embedding_relationships_dimension,
        }
        native_expected = {768} if self.gemma else expected
        if dimensions != native_expected:
            raise ValueError(
                f"Ollama embedding dimension {next(iter(dimensions))} does not "
                f"match native dimensions {sorted(native_expected)}; reindex existing "
                "data when changing embedding models"
            )
        if self.gemma:
            if expected != {512} or config.embeddings.native_dimension != 768 or config.embeddings.mrl_dimension != 512:
                raise ValueError("EmbeddingGemma 2 requires native 768 and stored/MRL 512 dimensions")
            transformed = []
            for vector in embeddings:
                truncated = vector[:512]
                norm = math.hypot(*truncated)
                if not math.isfinite(norm) or norm == 0:
                    raise ValueError("Cannot normalize a zero or non-finite MRL embedding")
                normalized = [value / norm for value in truncated]
                if len(normalized) != 512 or not all(math.isfinite(value) for value in normalized):
                    raise ValueError("Invalid normalized 512-dimensional embedding")
                transformed.append(normalized)
            return transformed
        return embeddings


_embeddings_ollama_client = OllamaEmbeddingsClient()
