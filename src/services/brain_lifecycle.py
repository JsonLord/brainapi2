"""Profile-aware lifecycle operations for BrainAPI brains."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
from typing import Any, Iterator, Protocol

from src.services.brain_identity import (
    BrainNotFoundError, InvalidBrainIdError, validate_brain_id,
)


logger = logging.getLogger(__name__)


class BrainLifecycleError(RuntimeError):
    """Base class for lifecycle failures safe to map at the API boundary."""


class BrainLifecycleBusyError(BrainLifecycleError):
    """Raised when another lifecycle operation already owns the brain lock."""


class BrainCleanupError(BrainLifecycleError):
    """Raised after one or more storage backends fail to clear."""

    def __init__(self, brain_id: str, failed_backends: list[str]):
        self.brain_id = brain_id
        self.failed_backends = failed_backends
        super().__init__(
            "Could not clear every storage backend for brain "
            f'"{brain_id}": {", ".join(failed_backends)}'
        )


class DataLifecycleClient(Protocol):
    def get_brain(self, name_key: str) -> Any: ...

    def clear_brain_data(self, brain_id: str) -> Any: ...

    def delete_brain_registry(self, brain_id: str) -> bool: ...


class StorageLifecycleClient(Protocol):
    def clear_brain_data(self, brain_id: str) -> Any: ...


class CacheLifecycleClient(StorageLifecycleClient, Protocol):
    client: Any

    def clear_brain_registry_cache(self, brain_id: str) -> Any: ...


@dataclass(frozen=True)
class BrainLifecycleResult:
    brain_id: str
    operation: str
    existed: bool
    cleared_backends: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": True,
            "brain_id": self.brain_id,
            "operation": self.operation,
            "existed": self.existed,
            "cleared_backends": self.cleared_backends,
        }


class BrainLifecycleService:
    """Clear or delete one brain across the active storage profile."""

    def __init__(
        self,
        *,
        data_client: DataLifecycleClient,
        graph_client: StorageLifecycleClient,
        vector_client: StorageLifecycleClient,
        cache_client: CacheLifecycleClient,
    ) -> None:
        self._data = data_client
        self._graph = graph_client
        self._vector = vector_client
        self._cache = cache_client

    @contextmanager
    def _lock(self, brain_id: str) -> Iterator[None]:
        redis = getattr(self._cache, "client", None)
        if redis is None or not hasattr(redis, "lock"):
            yield
            return

        lock = redis.lock(
            f"system:brain-lifecycle:{brain_id}",
            timeout=300,
            blocking_timeout=1,
        )
        if not lock.acquire(blocking=True):
            raise BrainLifecycleBusyError(
                f'Another lifecycle operation is running for brain "{brain_id}"'
            )
        try:
            yield
        finally:
            try:
                lock.release()
            except Exception:
                # The lock has a TTL and may already have expired. Cleanup must not
                # turn a successful destructive operation into a false failure.
                pass

    def _clear_backends(self, brain_id: str) -> list[str]:
        backends = (
            ("data", self._data),
            ("graph", self._graph),
            ("vectors", self._vector),
            ("cache", self._cache),
        )
        cleared: list[str] = []
        failed: list[str] = []

        for name, backend in backends:
            try:
                backend.clear_brain_data(brain_id)
                cleared.append(name)
            except Exception:
                logger.exception(
                    "Failed to clear %s backend for brain %s",
                    name,
                    brain_id,
                )
                failed.append(name)

        if failed:
            raise BrainCleanupError(brain_id, failed)
        return cleared

    def clear(self, brain_id: str) -> BrainLifecycleResult:
        brain_id = validate_brain_id(brain_id)
        with self._lock(brain_id):
            if self._data.get_brain(brain_id) is None:
                raise BrainNotFoundError(f'Brain "{brain_id}" was not found')
            cleared = self._clear_backends(brain_id)
        return BrainLifecycleResult(
            brain_id=brain_id,
            operation="clear",
            existed=True,
            cleared_backends=cleared,
        )

    def delete(self, brain_id: str) -> BrainLifecycleResult:
        brain_id = validate_brain_id(brain_id)
        with self._lock(brain_id):
            existed = self._data.get_brain(brain_id) is not None
            cleared = self._clear_backends(brain_id)
            try:
                self._cache.clear_brain_registry_cache(brain_id)
            except Exception:
                logger.exception(
                    "Failed to clear registry cache for brain %s", brain_id
                )
                raise BrainCleanupError(brain_id, ["registry_cache"]) from None
            try:
                # PostgreSQL namespaces must disappear even when registry-less.
                delete_storage = getattr(self._data, "delete_brain_storage", None)
                if delete_storage is not None:
                    delete_storage(brain_id)
                self._data.delete_brain_registry(brain_id)
            except Exception:
                logger.exception("Failed to delete registry for brain %s", brain_id)
                raise BrainCleanupError(brain_id, ["registry"]) from None

        return BrainLifecycleResult(
            brain_id=brain_id,
            operation="delete",
            existed=existed,
            cleared_backends=cleared,
        )
