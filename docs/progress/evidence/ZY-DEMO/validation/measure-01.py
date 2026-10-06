import json
import time
from pathlib import Path
from urllib.request import urlopen

base = "http://127.0.0.1:19174"
expected = "bf_test_c5abb6be63334a0e8a54a0b749a22d47"
result = {"environment": expected, "state_ms": [], "preview_ms": []}
state = None
for index in range(5):
    started = time.perf_counter()
    with urlopen(base + "/api/v1/zhiyu/state", timeout=60) as response:
        state = json.load(response)
    assert state["environment_id"] == expected and state["simulation"] is True
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    result["state_ms"].append(elapsed)
    print("state", index + 1, elapsed, flush=True)
assert state is not None and len(state["goals"]) == 1
goal = state["goals"][0]["id"]
for index in range(5):
    started = time.perf_counter()
    with urlopen(base + f"/api/v1/goals/{goal}/allocation-preview", timeout=60) as response:
        preview = json.load(response)
    assert preview["goal_id"] == goal and preview["allocation"]["suggested_cents"] == 100000
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    result["preview_ms"].append(elapsed)
    print("preview", index + 1, elapsed, flush=True)
result["goal_id"] = goal
result["baseline"] = {"cash_cents":state["dashboard"]["account_facts"]["facts"]["summary"]["cash_balance_cents"]} if "summary" in state["dashboard"]["account_facts"]["facts"] else {}
Path(".runtime/zhiyu-validation/read-timings-01.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
Path(".runtime/zhiyu-validation/AB1-before.json").write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
