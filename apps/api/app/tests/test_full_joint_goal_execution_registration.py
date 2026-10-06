"""Actual Main and transaction registration; service doubles are not PG evidence."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from unittest.mock import MagicMock

import pytest
from app.api import dependencies
from app.main import create_app
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.requests import Request


@pytest.mark.parametrize(
    ("method", "path", "schema_path", "operation"),
    [
        (
            "GET",
            "/by-key/original",
            "/by-key/{idempotency_key}",
            "lookup_original_joint_goal_plan_key",
        ),
        ("GET", "/original", "/{plan_id}", "read_original_joint_goal_plan"),
        ("POST", "/preview", "/preview", "preview_registered_joint_goal_fixed_plan"),
    ],
)
def test_joint_original_get_and_preview_use_clean_repeatable_read_only_snapshot(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, schema_path: str, operation: str
) -> None:
    prefix = "/api/v1/joint-goal-actions"
    assert create_app().openapi()["paths"][prefix + schema_path][method.lower()]["operationId"] == (
        operation
    )
    session = cast(Session, MagicMock(spec=Session))
    engine = cast(Engine, MagicMock(spec=Engine))

    @contextmanager
    def original_session(target: Engine) -> Iterator[Session]:
        assert target is engine
        yield session

    monkeypatch.setattr(dependencies, "database_session", original_session)
    request = Request({"type": "http", "method": method, "path": prefix + path, "headers": []})
    yielded = dependencies.get_session(request, engine)
    assert next(yielded) is session
    mock = cast(MagicMock, session)
    mock.connection.assert_called_once_with(
        execution_options={"isolation_level": "REPEATABLE READ"}
    )
    assert [str(call.args[0]) for call in mock.execute.call_args_list] == [
        "SET TRANSACTION READ ONLY"
    ]
    with pytest.raises(StopIteration):
        next(yielded)
    mock.commit.assert_not_called()
    mock.flush.assert_not_called()
