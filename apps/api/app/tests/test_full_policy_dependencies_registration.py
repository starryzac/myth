"""Actual Main registration and clean read transaction; not PostgreSQL evidence."""

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


def test_current_full_policy_dependencies_use_clean_repeatable_read_only_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = "/api/v1/full-policy-dependencies"
    operation = create_app().openapi()["paths"][prefix + "/{policy_id}"]["get"]
    assert operation["operationId"] == "read_current_full_policy_dependencies"
    session = cast(Session, MagicMock(spec=Session))
    engine = cast(Engine, MagicMock(spec=Engine))

    @contextmanager
    def original_session(target: Engine) -> Iterator[Session]:
        assert target is engine
        yield session

    monkeypatch.setattr(dependencies, "database_session", original_session)
    request = Request(
        {"type": "http", "method": "GET", "path": prefix + "/original", "headers": []}
    )
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
