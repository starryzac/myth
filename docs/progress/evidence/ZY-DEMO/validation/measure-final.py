"""Frozen AB3 HTTP reads; deliberately contains no valid financial write."""

import json
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:19005"
ENVIRONMENT = "bf_test_b43172f572614275968085edd60b57ed"
EPOCH = "084e1103-d5f4-4f35-9d8c-f32ab9e7391f"
ACTION = "d1b9da6d-8588-4298-9d48-11bdab57c13a"
EFFECT = "9c2d95b11e089f5b7fbdb9b98d0fe298b694d943f676cda88c63a452b9f1d2af"
TARGET = Path(".runtime/zhiyu-validation/read-timings-final.json")
phase = sys.argv[1]
assert phase in {"before_execute", "after_execute"}
result = json.loads(TARGET.read_text(encoding="utf-8")) if TARGET.exists() else {
    "base_url": BASE, "environment_id": ENVIRONMENT, "epoch_id": EPOCH,
    "action_id": ACTION, "effect_hash": EFFECT,
    "method": "Sequential urllib HTTP request plus body download and JSON parsing; no browser rendering, no valid financial writes, no PG test suite.",
}
assert result["environment_id"] == ENVIRONMENT and result["action_id"] == ACTION
group = {"started_at": datetime.now(UTC).isoformat(), "state": [], "allocation_preview": []}
result[phase] = group

def save():
    TARGET.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

def read(path):
    start = time.perf_counter()
    with urlopen(BASE + path, timeout=90) as response:
        raw = response.read()
        status = response.status
    downloaded = time.perf_counter()
    value = json.loads(raw)
    end = time.perf_counter()
    return value, {"http_status": status, "http_ms": round((downloaded-start)*1000, 3),
                   "json_parse_ms": round((end-downloaded)*1000, 3),
                   "total_ms": round((end-start)*1000, 3), "response_bytes": len(raw)}

def projection(state):
    return {"accounts": sorted((row["id"], row["balance_cents"]) for row in state["dashboard"]["account_facts"]["facts"]["accounts"]),
            "goals": [(row["id"], row["allocated_cents"]) for row in state["goals"]],
            "actions": [(row["action_id"], row["effect_hash"], row["status"], row["bank_status"], row["receipt"]) for row in state["actions"]],
            "income_received": state["income_received"]}

def cheap_checks(state):
    schema, _ = read("/openapi.json")
    allowed_get = [r"/api/v1/health", r"/api/v1/zhiyu/(environment|presets|state)",
                   r"/api/v1/policies", r"/api/v1/policy-proposals", r"/api/v1/policy-compilations/[^/]+",
                   r"/api/v1/actions/[^/]+", r"/api/v1/goals/[^/]+/allocation-preview"]
    allowed_post = [r"/api/v1/policies/compile", r"/api/v1/policy-proposals/[^/]+/confirm",
                    r"/api/v1/policies/[^/]+/(suspend|revoke)", r"/api/v1/actions/[^/]+/confirm",
                    r"/api/v1/zhiyu/(goal|income|actions/prepare)", r"/api/v1/zhiyu/actions/[^/]+/execute"]
    assert "/api/v1/zhiyu/state" in schema["paths"]
    assert all(any(re.fullmatch(pattern, path) for pattern in {"get":allowed_get,"post":allowed_post}.get(method, []))
               for path, operations in schema["paths"].items() for method in operations)
    checks = {"openapi_paths": {path: sorted(operations) for path, operations in sorted(schema["paths"].items())}, "requests": []}
    goal = state["goals"][0]
    cases = [("GET", "/api/v1/boundary/full-action-set", None, 404),
             ("GET", "/api/v1/dashboard", None, 404),
             ("GET", "/api/v1/demo/state", None, 404),
             ("GET", "/api/v1/zhiyu/state?now=2026-01-01T00:00:00Z", None, 422),
             ("POST", "/api/v1/zhiyu/income", {"expected_epoch_id": EPOCH, "amount_cents": 1}, 422),
             ("POST", "/api/v1/zhiyu/actions/prepare", {"expected_epoch_id": EPOCH, "goal_id": goal["id"], "scenario": "SAFE", "now": "2026-01-01T00:00:00Z"}, 422),
             ("POST", f"/api/v1/zhiyu/actions/{ACTION}/execute", {"receipt": {"executed_cents": 100000}}, 422),
             ("POST", "/api/v1/zhiyu/goal", {"policy_id": goal["policy_id"], "expected_version_id": goal["policy_version_id"], "amount_cents": 100000}, 422)]
    for method, path, body, expected in cases:
        request = Request(BASE+path, method=method, data=None if body is None else json.dumps(body).encode(),
                          headers={} if body is None else {"Content-Type":"application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                actual, raw = response.status, response.read()
        except HTTPError as error:
            actual, raw = error.code, error.read()
        assert actual == expected, (method,path,actual)
        value = json.loads(raw)
        if body is not None:
            assert value["error"]["code"] == "VALIDATION_ERROR", (path,value)
        checks["requests"].append({"method":method,"path":path,"http_status":actual,"error_code":value.get("error",{}).get("code")})
    result["cheap_contract_checks"] = checks

state = None
for index in range(5):
    state, sample = read("/api/v1/zhiyu/state")
    assert state["simulation"] is True and state["environment_id"] == ENVIRONMENT and state["epoch_id"] == EPOCH
    assert len(state["goals"]) == 1
    action = next(row for row in state["actions"] if row["action_id"] == ACTION)
    assert action["effect_hash"] == EFFECT and action["user_id"] == state["dashboard"]["user_id"]
    assert action["effect"]["amount_cents"] == 100000
    if phase == "before_execute":
        assert action["status"] in {"PLANNED", "AUTHORIZED"} and action["receipt"] is None
        assert state["goals"][0]["allocated_cents"] == 0
    else:
        assert action["status"] == "SUCCEEDED" and action["bank_status"] == "SETTLED" and action["receipt"] is not None
        assert state["goals"][0]["allocated_cents"] == 100000
    current = projection(state)
    if index == 0:
        group["financial_projection"] = current
    else:
        assert current == group["financial_projection"], "Fixed read-only data changed during measurement"
    sample.update(index=index+1, action_status=action["status"], goal_allocated_cents=state["goals"][0]["allocated_cents"])
    group["state"].append(sample)
    save()
    print(phase, "state", index+1, sample["total_ms"], flush=True)
    if phase == "after_execute" and index == 0:
        cheap_checks(state)
        save()
        print("cheap contract checks passed; subsequent state reads must retain the same financial projection", flush=True)
assert state is not None
goal = state["goals"][0]["id"]
result["goal_id"] = goal
for index in range(5):
    preview, sample = read(f"/api/v1/goals/{goal}/allocation-preview")
    assert preview["simulation"] is True and preview["goal_id"] == goal and preview["user_id"] == state["dashboard"]["user_id"]
    assert preview["allocation"]["preview_only"] is True and preview["allocation"]["financial_only"] is True
    sample.update(index=index+1, allocation_status=preview["allocation"]["status"], suggested_cents=preview["allocation"]["suggested_cents"])
    group["allocation_preview"].append(sample)
    save()
    print(phase, "preview", index+1, sample["total_ms"], flush=True)
for endpoint in ("state", "allocation_preview"):
    values = [sample["total_ms"] for sample in group[endpoint]]
    group[endpoint+"_summary_ms"] = {"min": min(values), "median": statistics.median(values), "max": max(values)}
group["finished_at"] = datetime.now(UTC).isoformat()
save()
