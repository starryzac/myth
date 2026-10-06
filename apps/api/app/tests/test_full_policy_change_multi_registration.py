"""Main/transaction dependency seam; not actual PostgreSQL proof."""

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


@pytest.mark.parametrize("source_kind", ["MVP_POLICY", "FULL_POLICY"])
def test_registered_preview_uses_read_only_repeatable_snapshot(
    monkeypatch: pytest.MonkeyPatch, source_kind: str
) -> None:
    path = "/api/v1/policy-financial-previews/{source_kind}/{policy_id}"
    assert create_app().openapi()["paths"][path]["post"]["operationId"] == (
        "preview_multi_template_policy_financial_change"
    )
    session = cast(Session, MagicMock(spec=Session))
    engine = cast(Engine, MagicMock(spec=Engine))

    @contextmanager
    def original_session(target: Engine) -> Iterator[Session]:
        assert target is engine
        yield session

    monkeypatch.setattr(dependencies, "database_session", original_session)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/v1/policy-financial-previews/{source_kind}/owned-policy",
            "headers": [],
        }
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
