"""Timing-only wrappers around the actual unchanged golden test, with no profiles."""
import json
import time
from datetime import UTC, datetime
from pathlib import Path

OUT = Path('.runtime/MVP-401-golden-optimized-phases.json')
records = []


def measured(label, function, *args, **kwargs):
    start = time.monotonic()
    stamp = datetime.now(UTC).isoformat()
    try:
        return function(*args, **kwargs)
    finally:
        records.append({'phase': label, 'started_at': stamp,
                        'seconds': time.monotonic() - start})
        OUT.write_text(json.dumps(records, indent=2), encoding='utf-8')


def pytest_sessionstart(session):
    from app.tests.dashboard_scenario import DashboardScenario
    from fastapi.testclient import TestClient
    original_initialize = DashboardScenario.initialize
    original_advance = DashboardScenario.advance
    original_get = TestClient.get

    def initialize(self, *args, **kwargs):
        return measured('seed', original_initialize, self, *args, **kwargs)

    def advance(self, stage, *args, **kwargs):
        return measured('funds-' + stage, original_advance, self, stage, *args, **kwargs)

    def get(self, url, *args, **kwargs):
        return measured('http-get-' + str(url), original_get, self, url, *args, **kwargs)

    DashboardScenario.initialize = initialize
    DashboardScenario.advance = advance
    TestClient.get = get
