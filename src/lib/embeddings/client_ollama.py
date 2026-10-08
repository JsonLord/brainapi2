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

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.post(
            "/api/embed",
            json={
                "model": self.model,
                "input": texts,
                "truncate": False,
                "keep_alive": config.ollama.embedding_keep_alive,
                "options": {"num_thread": config.ollama.num_threads},
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
        if dimensions != expected:
            raise ValueError(
                f"Ollama embedding dimension {next(iter(dimensions))} does not "
                f"match configured dimensions {sorted(expected)}; reindex existing "
                "data when changing embedding models"
            )
        return embeddings


_embeddings_ollama_client = OllamaEmbeddingsClient()
