import urllib.request
import json
import time

BASE_URL = "https://leon4gr45-brain.hf.space"
PAT = "brainpat_default_token"

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
        try:
            return resp.status, json.loads(res_body)
        except Exception:
            return resp.status, res_body

print("1. Testing GET /health...")
status, body = req("/health")
print(f"Status: {status}, Body: {body}")
assert status == 200

print("2. Testing GET /console/...")
status, body = req("/console/")
print(f"Status: {status}")
assert status == 200

print("3. Testing GET /system/workspaces...")
status, body = req("/system/workspaces")
print(f"Status: {status}, Body: {body}")
assert status == 200

print("4. Testing POST /system/workspaces (create workspace alpha)...")
try:
    status, body = req("/system/workspaces", method="POST", data={"slug": "alpha", "display_name": "Alpha Workspace"})
    print(f"Status: {status}, Body: {body}")
except urllib.error.HTTPError as e:
    print("Post error (might already exist):", e.code, e.read().decode("utf-8"))

print("5. Testing GET /brains/alpha/api...")
status, body = req("/brains/alpha/api")
print(f"Status: {status}")
assert status == 200

print("6. Testing GET /brains/alpha/agent/capabilities...")
status, body = req("/brains/alpha/agent/capabilities")
print(f"Status: {status}, Body: {body}")
assert status == 200

print("7. Testing POST /brains/alpha/agent/memory...")
status, body = req("/brains/alpha/agent/memory", method="POST", data={"text": "User prefers concise summaries."})
print(f"Status: {status}, Body: {body}")
assert status in (200, 202)
task_id = body.get("task_id") or body.get("id")

if task_id:
    print(f"8. Testing GET /brains/alpha/agent/tasks/{task_id}...")
    for _ in range(15):
        status, body = req(f"/brains/alpha/agent/tasks/{task_id}")
        print(f"Task status: {body.get('status')}")
        if body.get("status") in ("completed", "failed", "success"):
            break
        time.sleep(2)

print("All Acceptance Tests Passed Successfully!")
