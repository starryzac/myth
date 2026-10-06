"""TOOL_TEST_ONLY call-bound fault tests; no connection, SQL or financial evaluation."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from app.services import execution, scenario_runner
from app.services import experiment_arms as CANDIDATE
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PATH = Path(CANDIDATE.__file__).resolve()


class Bridge:
    def __init__(self, fault: str = "NONE", kind: str = "EXECUTE_ACTION") -> None:
        self.step = {"fault": fault, "kind": kind}
        self.source_paths = {
            "apps/api/app/services/scenario_runner.py": hashlib.sha256(
                Path(scenario_runner.__file__ or "").read_bytes()
            ).hexdigest()
        }
        self.checkpoints = 0

    def checkpoint(self) -> None:
        self.checkpoints += 1


@pytest.fixture
def engines() -> Iterator[list[Engine]]:
    values = [
        create_engine("postgresql+psycopg://unused@127.0.0.1:54329/bf_test_" + uuid4().hex)
        for _ in range(2)
    ]
    try:
        yield values
    finally:
        for value in values:
            value.dispose()


@pytest.mark.parametrize("mode", ["V1", "NONE"])
def test_default_has_no_fault_import_hook_or_sql(
    mode: str, engines: list[Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No fault hooks or database connection permitted")

    monkeypatch.setattr(scenario_runner, "_fault", unexpected)
    for engine in engines:
        monkeypatch.setattr(engine, "connect", unexpected)
    bridge = Bridge()
    registry = {} if mode == "V1" else {"_invocation_bridge": bridge}
    with CANDIDATE._execution_fault_scope(registry, engines[0], uuid4(), str(uuid4())):
        pass
    assert bridge.checkpoints == (0 if mode == "V1" else 1)


def test_root_engine_fault_does_not_hit_provider_engine_but_own_scope_does(
    engines: list[Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Engine, UUID]] = []
    owner = uuid4()

    def original(engine: Engine, user: UUID, *args: Any, **kwargs: Any) -> dict[str, str]:
        calls.append((engine, user))
        return {"transport_test_only": "original_return"}

    monkeypatch.setattr(execution, "process_operation", original)
    with scenario_runner._fault("DROP_BANK_RESPONSE", "EXECUTE_ACTION", engines[0], owner):
        assert execution.__dict__["process_operation"](
            engines[1], owner, uuid4(), datetime.now(UTC)
        )
    assert len(calls) == 1
    bridge = Bridge("DROP_BANK_RESPONSE")
    with CANDIDATE._execution_fault_scope(
        {"_invocation_bridge": bridge}, engines[1], owner, str(uuid4())
    ):
        with pytest.raises(TimeoutError, match="SIMULATED_BANK_RESPONSE_LOST"):
            execution.__dict__["process_operation"](engines[1], owner, uuid4(), datetime.now(UTC))
    assert len(calls) == 2 and calls[1] == (engines[1], owner)
    assert execution.__dict__["process_operation"] is original


@pytest.mark.parametrize("foreign", ["ENGINE", "OWNER"])
def test_other_engine_or_owner_and_after_scope_keep_original_call(
    foreign: str, engines: list[Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = uuid4()
    sentinel = object()

    def original(*args: Any, **kwargs: Any) -> Any:
        return sentinel

    monkeypatch.setattr(execution, "process_operation", original)
    with CANDIDATE._execution_fault_scope(
        {"_invocation_bridge": Bridge("DROP_BANK_RESPONSE")}, engines[0], owner, str(uuid4())
    ):
        target_engine = engines[1] if foreign == "ENGINE" else engines[0]
        target_owner = uuid4() if foreign == "OWNER" else owner
        assert (
            execution.__dict__["process_operation"](
                target_engine, target_owner, uuid4(), datetime.now(UTC)
            )
            is sentinel
        )
    assert (
        execution.__dict__["process_operation"](engines[0], owner, uuid4(), datetime.now(UTC))
        is sentinel
    )


def test_projection_uses_original_session_bind_and_restores_hook(
    engines: list[Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = uuid4()
    sentinel = object()

    def original(*args: Any, **kwargs: Any) -> Any:
        return sentinel

    monkeypatch.setattr(execution, "project_execution", original)
    with Session(engines[0]) as own, Session(engines[1]) as other:
        with CANDIDATE._execution_fault_scope(
            {"_invocation_bridge": Bridge("FAIL_APPLICATION_PROJECTION")},
            engines[0],
            owner,
            str(uuid4()),
        ):
            assert (
                execution.__dict__["project_execution"](other, SimpleNamespace(user_id=owner))
                is sentinel
            )
            assert (
                execution.__dict__["project_execution"](own, SimpleNamespace(user_id=uuid4()))
                is sentinel
            )
            with pytest.raises(
                PolicyLifecycleError, match="SIMULATED_APPLICATION_PROJECTION_INTERRUPTION"
            ):
                execution.__dict__["project_execution"](own, SimpleNamespace(user_id=owner))
        assert (
            execution.__dict__["project_execution"](own, SimpleNamespace(user_id=owner)) is sentinel
        )
    assert execution.__dict__["project_execution"] is original


@pytest.mark.parametrize("case", ["KIND", "FAULT", "SOURCE", "FUNCTION", "WRAPPER"])
def test_unregistered_or_hijacked_fault_is_refused_before_hook_install(
    case: str, engines: list[Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = Bridge("DROP_BANK_RESPONSE")
    original_process = execution.__dict__["process_operation"]
    if case == "KIND":
        bridge.step["kind"] = "RUN_RECOVERY"
    elif case == "FAULT":
        bridge.step["fault"] = "SYNTHETIC_SUCCESS"
    elif case == "SOURCE":
        bridge.source_paths.clear()
    elif case == "FUNCTION":

        @contextmanager
        def fake(*args: Any, **kwargs: Any) -> Any:
            yield

        monkeypatch.setattr(scenario_runner, "_fault", fake)
    else:
        old = scenario_runner._fault

        def counterfeit(*args: Any, **kwargs: Any) -> Any:
            return old(*args, **kwargs)

        cast(Any, counterfeit).__wrapped__ = cast(Any, old).__wrapped__
        counterfeit.__module__ = old.__module__
        monkeypatch.setattr(scenario_runner, "_fault", counterfeit)
    with pytest.raises(CANDIDATE.ProviderNotImplemented):
        CANDIDATE._execution_fault_scope(
            {"_invocation_bridge": bridge}, engines[0], uuid4(), str(uuid4())
        )
    assert execution.__dict__["process_operation"] is original_process


def test_provider_keeps_original_execute_four_position_call() -> None:
    import ast

    source = ast.parse(PATH.read_text(encoding="utf-8"))
    execute = next(
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "execute_arm_action"
    )
    calls = [
        node
        for node in ast.walk(execute)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute_action"
    ]
    assert len(calls) == 1 and len(calls[0].args) == 4 and calls[0].keywords == []
