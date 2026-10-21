#!/usr/bin/env python3
"""Idempotently seed the dedicated public demo brain from published V2 docs."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.brain_identity import InvalidBrainIdError, validate_brain_id


DEFAULT_DOCS_URL = "https://brainapi.lumen-labs.ai/docs/llms-full.txt"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=os.getenv("BRAINAPI_URL", "http://localhost:8000"))
    parser.add_argument("--docs-url", default=DEFAULT_DOCS_URL)
    parser.add_argument("--brain", default=os.getenv("PUBLIC_DEMO_BRAIN_ID", "agentdemo"))
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    from src.services.auth_config import resolve_system_pat
    token = resolve_system_pat()
    if not token:
        print("SYSTEM_PAT is required", file=sys.stderr)
        return 2
    try:
        args.brain = validate_brain_id(args.brain)
    except InvalidBrainIdError as error:
        print(str(error), file=sys.stderr)
        return 2

    api_url = args.api_url.rstrip("/")
    docs_response = requests.get(args.docs_url, timeout=30)
    docs_response.raise_for_status()
    docs_text = docs_response.text
    digest = hashlib.sha256(docs_text.encode("utf-8")).hexdigest()
    marker = f"BRAINAPI_PUBLIC_DEMO_SHA256_{digest}"
    headers = {"BrainPAT": token, "X-Brain-ID": args.brain}

    existing = requests.get(
        f"{api_url}/retrieve/text-chunks",
        headers=headers,
        params={"query_text": marker, "limit": 1},
        timeout=30,
    )
    if existing.status_code == 200 and existing.json().get("total", 0) > 0:
        print(f"Public demo brain {args.brain!r} is already current ({digest[:12]}).")
        return 0
    if existing.status_code in (404, 406) and existing.json().get("error", {}).get("code") == "BRAIN_NOT_FOUND":
        created = requests.post(
            f"{api_url}/system/brains", headers=headers,
            json={"brain_id": args.brain}, timeout=30,
        )
        if not (created.status_code == 409 and created.json().get("error", {}).get("code") == "BRAIN_ALREADY_EXISTS"):
            created.raise_for_status()
    else:
        existing.raise_for_status()

    payload = {
        "data": {
            "data_type": "text",
            "text_data": f"{marker}\nSource: {args.docs_url}\n\n{docs_text}",
        },
        "brain_id": args.brain,
        "skip_enrichment": True,
    }
    accepted = requests.post(
        f"{api_url}/ingest/",
        headers={
            **headers,
            "Content-Type": "application/json",
            "Task-Identifier": f"public-demo-{digest[:24]}",
        },
        json=payload,
        timeout=30,
    )
    accepted.raise_for_status()
    from src.services.task_contract import accepted_task_id
    task_id = accepted_task_id(accepted.status_code, accepted.json())

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        task_response = requests.get(
            f"{api_url}/tasks/{task_id}",
            headers=headers,
            timeout=30,
        )
        task_response.raise_for_status()
        task = task_response.json()
        status = str(task.get("status", "unknown")).lower()
        if status in {"completed", "success", "succeeded"}:
            print(f"Seeded public demo brain {args.brain!r} ({digest[:12]}).")
            return 0
        if status in {"failed", "error", "revoked"}:
            print(f"Public demo seed task failed: {task}", file=sys.stderr)
            return 1
        time.sleep(2)

    print(f"Timed out waiting for seed task {task_id}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
