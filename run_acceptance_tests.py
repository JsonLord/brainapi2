import os
import sys
import time
import json
import urllib.request
import urllib.error

BASE_URL = os.environ.get("BASE_URL", "https://leon4gr45-brain.hf.space")
PAT = os.environ.get("BRAINPAT_TOKEN")

if not PAT:
    print("ERROR: BRAINPAT_TOKEN environment variable is required.")
    sys.exit(1)

def req(path, method="GET", data=None, headers=None):
    url = f"{BASE_URL}{path}"
    h = {"User-Agent": "AcceptanceTest/1.0", "BrainPAT": PAT}
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        h["Content-Type"] = "application/json"

    r = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(r) as resp:
        res_body = resp.read().decode("utf-8")
        if "data: " in res_body:
            for line in res_body.splitlines():
                if line.startswith("data: "):
                    try:
                        return resp.status, json.loads(line[6:])
                    except Exception:
                        pass
        try:
            return resp.status, json.loads(res_body)
        except Exception:
            return resp.status, res_body

print("1. Testing GET /health...")
status, body = req("/health")
print(f"Status: {status}, Body: {body}")
assert status == 200, f"Expected 200, got {status}"

print("\n2. Testing GET /console/...")
status, body = req("/console/")
print(f"Status: {status}")
assert status == 200, f"Expected 200, got {status}"

print("\n3. Testing GET /system/workspaces...")
status, body = req("/system/workspaces")
print(f"Status: {status}, Body: {body}")
assert status == 200, f"Expected 200, got {status}"

print("\n4. Testing POST /system/workspaces (ensure workspace 'alpha')...")
try:
    status, body = req("/system/workspaces", method="POST", data={"slug": "alpha", "display_name": "Alpha Workspace"})
    print(f"Status: {status}, Body: {body}")
except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8")
    print(f"Workspace creation status: {e.code} ({err_body})")

print("\n5. Testing GET /brains/alpha/api...")
status, body = req("/brains/alpha/api")
print(f"Status: {status}")
assert status == 200, f"Expected 200, got {status}"

print("\n6. Testing GET /brains/alpha/agent/capabilities...")
status, body = req("/brains/alpha/agent/capabilities")
print(f"Status: {status}, Body: {body}")
assert status == 200, f"Expected 200, got {status}"

print("\n7. Testing POST /brains/alpha/agent/memory (writing test fact)...")
fact_key = f"quantum_resonance_key_{int(time.time())}"
test_fact = f"User preference fact: {fact_key}"
status, body = req("/brains/alpha/agent/memory", method="POST", data={"text": test_fact})
print(f"Status: {status}, Body: {body}")
assert status in (200, 202), f"Unexpected memory write status: {status}"

task_id = body.get("task_id") or body.get("id")
assert task_id, "Memory write response missing task_id"

print(f"\n8. Polling task status for task_id {task_id} until completion...")
final_status = None
for attempt in range(20):
    status, task_body = req(f"/brains/alpha/agent/tasks/{task_id}")
    print(f"Attempt {attempt+1}: status = {status}, body = {task_body}")
    task_state = task_body.get("status")
    if task_state in ("completed", "success", "failed", "partial_failed"):
        final_status = task_state
        break
    time.sleep(2)

print(f"Final task status: {final_status}")
assert final_status in ("completed", "success"), f"Task failed or did not complete cleanly: {final_status}"

print("\n9. Testing POST /brains/alpha/agent/context (verifying fact retrieval)...")
status, ctx_body = req("/brains/alpha/agent/context", method="POST", data={"query": fact_key})
print(f"Status: {status}, Body: {ctx_body}")
assert status == 200, f"Expected 200 for context query, got {status}"

ctx_str = json.dumps(ctx_body)
assert fact_key in ctx_str, f"Fact key '{fact_key}' not found in context response: {ctx_str}"
print(f"Fact '{fact_key}' successfully retrieved from memory!")

print("\n10. Testing MCP transport smoke test against /brains/alpha/mcp...")
mcp_init_payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "AcceptanceTest", "version": "1.0.0"}
    }
}
status, mcp_resp = req(
    "/brains/alpha/mcp",
    method="POST",
    data=mcp_init_payload,
    headers={"Accept": "application/json, text/event-stream"}
)
print(f"MCP Initialize Status: {status}, Response: {mcp_resp}")
assert status == 200, f"Expected 200 for MCP initialize, got {status}"
assert mcp_resp.get("jsonrpc") == "2.0", "Invalid JSON-RPC 2.0 response for MCP initialize"

mcp_tools_payload = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
}
status, mcp_tools_resp = req(
    "/brains/alpha/mcp",
    method="POST",
    data=mcp_tools_payload,
    headers={"Accept": "application/json, text/event-stream"}
)
print(f"MCP Tools List Status: {status}, Response: {mcp_tools_resp}")
assert status == 200, f"Expected 200 for MCP tools/list, got {status}"
assert mcp_tools_resp.get("jsonrpc") == "2.0", "Invalid JSON-RPC 2.0 response for MCP tools/list"

print("\nAll Live Acceptance Tests Passed Successfully!")
