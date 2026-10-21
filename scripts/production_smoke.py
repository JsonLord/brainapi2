#!/usr/bin/env python3
"""Exercise a running production profile and record release-gate artifacts."""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


TERMINAL_TASK_STATES = {"completed", "failed", "partial_failed"}
COUNT_PATHS = {
    "nodes": "/retrieve/entities?limit=1",
    "edges": "/retrieve/relationships?limit=1",
    "chunks": "/retrieve/text-chunks?limit=1",
    "observations": "/retrieve/observations?limit=1",
}


class Client:
    def __init__(self, base_url: str, system_token: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.system_token = system_token
        self.timeout = timeout
        self.unexpected_5xx = 0

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        brain_id: str | None = None,
        body: dict[str, Any] | None = None,
        expected: set[int] | None = None,
    ) -> tuple[int, Any, float]:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if brain_id:
            headers["X-Brain-ID"] = brain_id
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=payload, headers=headers, method=method
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            content_type = exc.headers.get("Content-Type", "")
        elapsed_ms = (time.perf_counter() - started) * 1000
        if status >= 500 and (expected is None or status not in expected):
            self.unexpected_5xx += 1
        if expected is not None and status not in expected:
            detail = raw.decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"{method} {path} returned {status}: {detail}")
        if "json" in content_type:
            result = json.loads(raw or b"null")
        else:
            result = raw.decode("utf-8", errors="replace")
        return status, result, elapsed_ms


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise RuntimeError("Cannot calculate a percentile without samples")
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(value, 3)


def _latency_summary(samples: list[float]) -> dict[str, float | int]:
    return {
        "samples": len(samples),
        "p50_ms": _percentile(samples, 0.50),
        "p95_ms": _percentile(samples, 0.95),
        "p99_ms": _percentile(samples, 0.99),
    }


def _write_merged(path: Path, key: str, value: dict[str, Any]) -> None:
    existing = json.loads(path.read_text()) if path.is_file() else {}
    existing[key] = value
    path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")


def _wait_for_task(
    client: Client, task_id: str, brain_id: str, token: str, timeout: float
) -> dict[str, Any]:
    if not isinstance(task_id, str) or task_id.strip().lower() in {"", "none", "null"}:
        raise ValueError("INGESTION_CONTRACT_ERROR: invalid task identifier")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, result, _ = client.request(
            "GET",
            f"/tasks/{task_id}",
            token=token,
            brain_id=brain_id,
            expected={200, 404},
        )
        if status == 200 and result.get("status") in TERMINAL_TASK_STATES:
            if result["status"] != "completed":
                raise RuntimeError(f"Task {task_id} ended as {result['status']}: {result}")
            return result
        time.sleep(1)
    raise RuntimeError(f"Task {task_id} did not complete within {timeout:g}s")


def _stage_ms(report: dict[str, Any] | None, name: str) -> float:
    for stage in (report or {}).get("stages", []):
        if stage.get("stage") == name:
            return float(stage.get("wall_ms", 0))
    raise RuntimeError(f"Profiled response did not include {name}")


def _online_llm_stages(report: dict[str, Any] | None) -> int:
    return sum(
        1
        for stage in (report or {}).get("stages", [])
        if "llm" in str(stage.get("stage", "")).lower()
    )


def _capture_counts(client: Client, brain_id: str, token: str) -> dict[str, int]:
    _, brains, _ = client.request(
        "GET", "/system/brains-list", token=client.system_token, expected={200}
    )
    counts = {"brains": len(brains)}
    for key, path in COUNT_PATHS.items():
        _, result, _ = client.request(
            "GET", path, token=token, brain_id=brain_id, expected={200}
        )
        counts[key] = int(result.get("total", result.get("count", 0)))
    _, stores, _ = client.request(
        "GET", "/retrieve/vectors/stores", token=token, brain_id=brain_id, expected={200}
    )
    vector_count = 0
    for store in stores.get("stores", []):
        name = store["name"]
        _, vectors, _ = client.request(
            "GET",
            f"/retrieve/vectors/{name}?limit=1",
            token=token,
            brain_id=brain_id,
            expected={200},
        )
        vector_count += int(vectors.get("total", 0))
    counts["vectors"] = vector_count
    return counts


def _search_check(client: Client, brain_id: str, token: str, query: str) -> dict[str, Any]:
    _, response, _ = client.request(
        "POST",
        "/retrieve/search",
        token=token,
        brain_id=brain_id,
        body={
            "query": query,
            "brain_id": brain_id,
            "k": 5,
            "channels": ["passages"],
            "profile_stages": True,
        },
        expected={200},
    )
    snippets = [str(hit.get("snippet", "")) for hit in response.get("hits", [])]
    return {"query": query, "match": any(query.lower() in item.lower() for item in snippets)}


def exercise(args: argparse.Namespace) -> None:
    artifacts = args.artifact_dir.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    client = Client(args.base_url, args.system_token, args.request_timeout)
    brain_id = args.brain_id or f"productiongate{int(time.time())}"
    markers = [f"releaseproof{i:02d}x" for i in range(10)]
    flows: dict[str, bool] = {}

    client.request(
        "GET",
        "/system/brains-list",
        token="malformed-token",
        expected={401},
    )
    flows["authentication"] = True

    _, brain, _ = client.request(
        "POST",
        "/system/brains",
        token=args.system_token,
        body={"brain_id": brain_id},
        expected={200},
    )
    _, issued, _ = client.request("POST", "/system/access-tokens", body={"brain_id": brain_id,
        "label": "production-smoke", "permissions": ["memory:read", "memory:write"]}, expected={201})
    brain_token = issued["token"]
    flows["brain_creation"] = brain.get("name_key") == brain_id

    source_text = "BrainAPI deterministic production proof: " + " ".join(markers)
    _, accepted, _ = client.request(
        "POST",
        "/ingest/",
        token=brain_token,
        brain_id=brain_id,
        body={
            "brain_id": brain_id,
            "data": {"data_type": "text", "text_data": source_text},
            "skip_enrichment": True,
        },
        expected={202},
    )
    _wait_for_task(client, accepted["task_id"], brain_id, brain_token, args.task_timeout)
    flows["plain_ingestion"] = True

    _, accepted, _ = client.request(
        "POST",
        "/ingest/structured",
        token=brain_token,
        brain_id=brain_id,
        body={
            "brain_id": brain_id,
            "mode": "deterministic",
            "data": [
                {
                    "subject": {"name": "Release runner", "type": "AGENT"},
                    "subj_event": {
                        "name": "VALIDATED",
                        "description": "validated the release profile",
                    },
                    "object": {"name": "BrainAPI", "type": "PRODUCT"},
                }
            ],
        },
        expected={202},
    )
    _wait_for_task(client, accepted["task_id"], brain_id, brain_token, args.task_timeout)
    flows["structured_ingestion"] = True
    flows["task_completion"] = True

    context_samples: list[float] = []
    search_samples: list[float] = []
    for index in range(args.warmups + args.samples):
        _, context, context_ms = client.request(
            "POST",
            "/retrieve/context",
            token=brain_token,
            brain_id=brain_id,
            body={
                "text": markers[index % len(markers)],
                "brain_id": brain_id,
                "profile_stages": True,
                "apply_fact_filter": False,
            },
            expected={200},
        )
        _, search, search_ms = client.request(
            "POST",
            "/retrieve/search",
            token=brain_token,
            brain_id=brain_id,
            body={
                "query": markers[index % len(markers)],
                "brain_id": brain_id,
                "k": 5,
                "channels": ["passages"],
                "profile_stages": True,
            },
            expected={200},
        )
        embed_ms = _stage_ms(search.get("stage_timings"), "embed.query")
        if index >= args.warmups:
            context_samples.append(context_ms)
            search_samples.append(max(0.0, search_ms - embed_ms))
    flows["context_retrieval"] = bool(context.get("text_context") is not None)
    flows["search"] = bool(search.get("hits"))

    _, console, _ = client.request("GET", "/console/", expected={200})
    flows["console"] = "<html" in console.lower() or "<!doctype" in console.lower()
    _, mcp, _ = client.request("GET", "/mcp/info", expected={200})
    flows["mcp"] = mcp.get("service") == "brainapi-mcp"

    checks = [_search_check(client, brain_id, brain_token, marker) for marker in markers]
    if not all(check["match"] for check in checks):
        raise RuntimeError("One or more deterministic retrieval spot checks failed")
    if not all(flows.values()):
        raise RuntimeError(f"Incomplete product flows: {flows}")
    if client.unexpected_5xx:
        raise RuntimeError(f"Smoke observed {client.unexpected_5xx} unexpected 5xx responses")

    _write_merged(
        artifacts / "smoke.json",
        args.profile,
        {"flows": flows, "unexpected_5xx": client.unexpected_5xx},
    )
    latency = {
        "context": {
            **_latency_summary(context_samples),
            "online_llm_retrieval_loops": _online_llm_stages(
                context.get("stage_timings")
            ),
        },
        "search": {**_latency_summary(search_samples), "excludes_embed_query": True},
    }
    _write_merged(artifacts / "latency.json", args.profile, latency)
    state = {
        "profile": args.profile,
        "brain_id": brain_id,
        "before": _capture_counts(client, brain_id, brain_token),
        "retrieval_spot_checks": checks,
    }
    (artifacts / f"state-{args.profile}.json").write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n"
    )
    print(brain_id)


def verify_restore(args: argparse.Namespace) -> None:
    artifacts = args.artifact_dir.resolve()
    state_path = artifacts / f"state-{args.profile}.json"
    state = json.loads(state_path.read_text())
    client = Client(args.base_url, args.system_token, args.request_timeout)
    brain_id = state["brain_id"]
    brain_token = args.system_token
    checks = [
        _search_check(client, brain_id, brain_token, item["query"])
        for item in state["retrieval_spot_checks"][:10]
    ]
    result = {
        "before": state["before"],
        "after": _capture_counts(client, brain_id, brain_token),
        "retrieval_spot_checks": checks,
    }
    output = artifacts / f"restore-{args.profile}.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if result["before"] != result["after"] or not all(item["match"] for item in checks):
        raise RuntimeError(f"{args.profile} restore verification failed; see {output}")
    print(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("exercise", "verify-restore"))
    parser.add_argument("--profile", required=True, choices=("light", "heavy"))
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument("--system-token", required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--brain-id")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--task-timeout", type=float, default=300)
    parser.add_argument("--request-timeout", type=float, default=60)
    args = parser.parse_args()
    if args.command == "exercise":
        exercise(args)
    else:
        verify_restore(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
