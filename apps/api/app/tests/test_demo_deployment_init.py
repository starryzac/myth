"""Owned deployment initialization is once-only and refuses non-demo databases."""

import runpy
from pathlib import Path

import pytest
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[4]
INITIALIZE = runpy.run_path(str(ROOT / "deploy/initialize_demo.py"))["initialize"]


@pytest.mark.integration
def test_owned_deployment_initializes_only_empty_database_then_preserves_every_row(
    demo_engine: Engine,
) -> None:
    first = INITIALIZE(demo_engine)
    assert first["status"] == "FRESH_DEMO_INITIALIZED" and first["seed_called"] is True
    original = database_snapshot(demo_engine)
    second = INITIALIZE(demo_engine)
    assert second["status"] == "EXISTING_HISTORY_RETAINED" and second["seed_called"] is False
    assert second["audit"]["status"] == "VALID"
    assert database_snapshot(demo_engine) == original


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://127.0.0.1:54329/bounded_funds",
        "postgresql+psycopg://db:54329/bf_test_" + "a" * 32,
        "postgresql+psycopg://127.0.0.1:5432/bf_test_" + "a" * 32,
    ],
)
def test_init_refuses_formal_remote_or_wrong_port_before_any_connection(url: str) -> None:
    engine = create_engine(url)
    try:
        with pytest.raises(ValueError):
            INITIALIZE(engine)
    finally:
        engine.dispose()
