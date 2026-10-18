from __future__ import annotations

import pytest

from src.services.brain_lifecycle import (
    BrainCleanupError,
    BrainLifecycleResult,
    BrainLifecycleService,
    BrainNotFoundError,
    InvalidBrainIdError,
    validate_brain_id,
)


class FakeStorage:
    def __init__(self, name: str, events: list[str], fail: bool = False):
        self.name = name
        self.events = events
        self.fail = fail

    def clear_brain_data(self, brain_id: str) -> None:
        self.events.append(f"clear:{self.name}:{brain_id}")
        if self.fail:
            raise RuntimeError(f"{self.name} unavailable")


class FakeData(FakeStorage):
    def __init__(
        self,
        events: list[str],
        *,
        exists: bool = True,
        fail: bool = False,
    ):
        super().__init__("data", events, fail)
        self.exists = exists

    def get_brain(self, name_key: str):
        self.events.append(f"get:{name_key}")
        return {"name_key": name_key, "pat": "pat"} if self.exists else None

    def delete_brain_registry(self, brain_id: str) -> bool:
        self.events.append(f"registry:delete:{brain_id}")
        was_present = self.exists
        self.exists = False
        return was_present


class FakeCache(FakeStorage):
    client = None

    def __init__(self, events: list[str], fail: bool = False):
        super().__init__("cache", events, fail)

    def clear_brain_registry_cache(self, brain_id: str) -> None:
        self.events.append(f"registry-cache:clear:{brain_id}")


def service(
    events: list[str],
    *,
    exists: bool = True,
    failing_backend: str | None = None,
) -> tuple[BrainLifecycleService, FakeData]:
    data = FakeData(events, exists=exists, fail=failing_backend == "data")
    return (
        BrainLifecycleService(
            data_client=data,
            graph_client=FakeStorage(
                "graph", events, fail=failing_backend == "graph"
            ),
            vector_client=FakeStorage(
                "vectors", events, fail=failing_backend == "vectors"
            ),
            cache_client=FakeCache(events, fail=failing_backend == "cache"),
        ),
        data,
    )


def test_clear_preserves_brain_identity_and_clears_every_backend():
    events: list[str] = []
    lifecycle, data = service(events)

    result = lifecycle.clear("demo")

    assert result == BrainLifecycleResult(
        brain_id="demo",
        operation="clear",
        existed=True,
        cleared_backends=["data", "graph", "vectors", "cache"],
    )
    assert data.exists is True
    assert "registry:delete:demo" not in events


def test_clear_rejects_an_unknown_brain_without_touching_storage():
    events: list[str] = []
    lifecycle, _ = service(events, exists=False)

    with pytest.raises(BrainNotFoundError):
        lifecycle.clear("missing")

    assert events == ["get:missing"]


def test_delete_is_idempotent_and_invalidates_registry_cache():
    events: list[str] = []
    lifecycle, data = service(events, exists=False)

    result = lifecycle.delete("gone")

    assert result.existed is False
    assert result.operation == "delete"
    assert data.exists is False
    assert events[-2:] == [
        "registry-cache:clear:gone",
        "registry:delete:gone",
    ]


def test_partial_cleanup_keeps_registry_for_a_safe_retry():
    events: list[str] = []
    lifecycle, data = service(events, failing_backend="graph")

    with pytest.raises(BrainCleanupError) as caught:
        lifecycle.delete("demo")

    assert caught.value.failed_backends == ["graph"]
    assert data.exists is True
    assert "clear:vectors:demo" in events
    assert "clear:cache:demo" in events
    assert "registry:delete:demo" not in events


@pytest.mark.parametrize("brain_id", ["", "bad id", "-bad", "system", "SYSTEM", "a" * 129])
def test_lifecycle_rejects_invalid_or_reserved_brain_ids(brain_id: str):
    with pytest.raises(InvalidBrainIdError):
        validate_brain_id(brain_id)
