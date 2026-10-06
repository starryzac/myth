"""Synthetic request-local capture and strict HTTP risks; no PG evidence."""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
from app.api import dependencies
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_action_set_boundary_registered as api
from app.domain.full_action_set_boundary_actual import derive_actual_action_set
from app.domain.full_action_set_boundary_registered import derive_registered_action_set
from app.main import create_app
from app.services import full_action_set_boundary_registered as service
from app.services.decision_recording import DecisionCapture
from app.services.full_action_set_boundary_actual import ActualActionSetCapture
from app.tests.test_full_action_set_boundary_registered import fixture
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.requests import Request


def test_all_current_family_producers_receive_the_same_single_actual_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = fixture()
    actual = data.recovery_composed.recovery.original_actual_input
    capture = ActualActionSetCapture(actual, derive_actual_action_set(actual), DecisionCapture())
    collect = MagicMock(return_value=capture)
    monkeypatch.setattr(service, "capture_actual_action_set", collect)
    session = cast(Session, MagicMock(spec=Session))
    family_inputs = (
        ("capture_current_periodic_payment_producers", data.recovery_composed.composed.periodic),
        ("capture_current_recovery_producers", data.recovery_composed.recovery),
        ("capture_current_release_producers", data.release),
        ("capture_current_joint_producers", data.joint),
    )
    readers: list[MagicMock] = []
    for name, inputs in family_inputs:
        reader = MagicMock(return_value=SimpleNamespace(inputs=inputs, originals=DecisionCapture()))
        monkeypatch.setattr(service, name, reader)
        readers.append(reader)
    result = service.capture_registered_action_set(session, actual.base.user_id, actual.base.as_of)
    assert result.inputs == data and result.result == derive_registered_action_set(data)
    collect.assert_called_once_with(session, actual.base.user_id, actual.base.as_of)
    for reader in readers:
        reader.assert_called_once_with(
            session, actual.base.user_id, actual.base.as_of, original_actual_capture=capture
        )
        assert reader.call_args.kwargs["original_actual_capture"] is capture


def test_registered_get_reads_only_current_server_owner_clock_and_rejects_extra_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = derive_registered_action_set(fixture())
    read = MagicMock(return_value=result)
    monkeypatch.setattr(api, "read_current_registered_action_set", read)
    app = FastAPI()
    app.include_router(api.router)
    session = MagicMock()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=result.user_id)
    app.dependency_overrides[get_now] = lambda: result.as_of
    with TestClient(app) as client:
        path = "/api/v1/boundary/registered-action-set/current"
        response = client.get(path)
        assert response.status_code == 200 and response.json() == result.model_dump(mode="json")
        read.assert_called_once_with(session, result.user_id, result.as_of)
        assert client.get(path + "?amount_cents=1").status_code == 422
        assert client.post(path, json={"bank_authority": True}).status_code == 405
        assert read.call_count == 1
    session.commit.assert_not_called()
    session.flush.assert_not_called()


def test_actual_main_mounts_current_and_dependency_enters_repeatable_read_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "/api/v1/boundary/registered-action-set/current"
    schema = create_app().openapi()
    assert schema["paths"][path]["get"]["operationId"] == ("read_current_registered_action_set_v5")
    session = MagicMock(spec=Session)
    engine = cast(Engine, MagicMock(spec=Engine))

    @contextmanager
    def original_session(target: Engine) -> Iterator[Session]:
        assert target is engine
        yield cast(Session, session)

    monkeypatch.setattr(dependencies, "database_session", original_session)
    request = Request({"type": "http", "method": "GET", "path": path, "headers": []})
    yielded = dependencies.get_session(request, engine)
    assert next(yielded) is session
    session.connection.assert_called_once_with(
        execution_options={"isolation_level": "REPEATABLE READ"}
    )
    assert [str(call.args[0]) for call in session.execute.call_args_list] == [
        "SET TRANSACTION READ ONLY"
    ]
    with pytest.raises(StopIteration):
        next(yielded)
    session.commit.assert_not_called()
    session.flush.assert_not_called()
