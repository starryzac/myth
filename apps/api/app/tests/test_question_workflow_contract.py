"""Actual router forwarding and strict answers using doubles; no bank outcome evidence."""

from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import question_workflow as api
from app.db.models import User
from app.services.question_workflow import (
    QuestionCommandLookupResponse,
    QuestionWorkflowResponse,
    _lookup_response,
    _response,
)
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_question_workflow import EPOCH, SESSION, original_records, revision
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_engine] = lambda: object()
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def start_body() -> dict[str, Any]:
    state = revision()
    return {
        "base_action_id": str(state.base_action_id),
        "variables": [row.model_dump(mode="json") for row in state.variables],
        "expected_epoch_id": str(EPOCH),
        "idempotency_key": "session-start",
    }


def answer_body() -> dict[str, Any]:
    state = revision()
    assert state.pending_question is not None
    return {
        "expected_epoch_id": str(EPOCH),
        "expected_revision": 1,
        "question_id": str(state.pending_question.question_id),
        "choice_key": "v0",
        "idempotency_key": "answer-original",
    }


def response() -> QuestionWorkflowResponse:
    state = revision()
    return QuestionWorkflowResponse(
        original_receipt=state,
        current_revision=state,
        effective_state="PENDING_ANSWER",
        pending_question=state.pending_question,
        replayed_original_receipt=False,
        current_source_fingerprint=state.source_fingerprint,
        fresh_evaluation_at=NOW,
    )


def test_router_uses_trusted_server_identity_clock_and_separate_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    def capture(*args: Any) -> QuestionWorkflowResponse:
        calls.append(args)
        return response()

    monkeypatch.setattr(api, "start_question_session", capture)
    monkeypatch.setattr(api, "answer_question_session", capture)
    monkeypatch.setattr(api, "read_question_session", capture)
    assert client.post("/api/v1/finite-planning/sessions", json=start_body()).status_code == 200
    assert calls[-1][1] == USER and calls[-1][-1] == NOW
    assert (
        client.post(
            f"/api/v1/finite-planning/sessions/{SESSION}/answers", json=answer_body()
        ).status_code
        == 200
    )
    assert calls[-1][1:3] == (USER, SESSION) and calls[-1][-1] == NOW
    assert calls[-1][3].choice_key == "v0"
    assert client.get(f"/api/v1/finite-planning/sessions/{SESSION}").status_code == 200
    assert calls[-1][1:3] == (USER, SESSION) and calls[-1][-1] == NOW
    assert len(calls) == 3


@pytest.mark.parametrize(
    "extra",
    [
        "user_id",
        "now",
        "facts",
        "result",
        "worlds",
        "bank_authority",
        "confirmation",
        "effect_hash",
        "balance_cents",
    ],
)
def test_no_client_source_or_grant_in_start_or_answer(client: TestClient, extra: str) -> None:
    assert (
        client.post(
            "/api/v1/finite-planning/sessions", json=start_body() | {extra: True}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/finite-planning/sessions/{SESSION}/answers",
            json=answer_body() | {extra: True},
        ).status_code
        == 422
    )


@pytest.mark.parametrize("value", [True, "1", 0, 17])
def test_revision_is_bounded_strict_integer(client: TestClient, value: Any) -> None:
    assert (
        client.post(
            f"/api/v1/finite-planning/sessions/{SESSION}/answers",
            json=answer_body() | {"expected_revision": value},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "missing",
    ["question_id", "choice_key", "expected_epoch_id", "expected_revision", "idempotency_key"],
)
def test_answer_requires_each_exact_binding(client: TestClient, missing: str) -> None:
    body = answer_body()
    body.pop(missing)
    assert (
        client.post(f"/api/v1/finite-planning/sessions/{SESSION}/answers", json=body).status_code
        == 422
    )


def test_no_query_overrides_and_no_unbounded_choice_domain(client: TestClient) -> None:
    assert client.get(f"/api/v1/finite-planning/sessions/{SESSION}?execute=true").status_code == 422
    assert (
        client.post(
            "/api/v1/finite-planning/sessions?user_id=foreign", json=start_body()
        ).status_code
        == 422
    )
    body = start_body()
    body["variables"][0]["source"] = "BANK_CONFIRMED"
    assert client.post("/api/v1/finite-planning/sessions", json=body).status_code == 422


def close_body() -> dict[str, Any]:
    return {
        "expected_epoch_id": str(EPOCH),
        "expected_revision": 1,
        "idempotency_key": "close-original",
    }


def test_close_and_both_key_gets_forward_only_trusted_owner_clock_and_original_identity(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = original_records()
    closed = records[-1][1]
    calls: list[tuple[Any, ...]] = []

    def close(*args: Any) -> QuestionWorkflowResponse:
        calls.append(args)
        return _response(closed, closed, None, replay=False)

    def start_lookup(*args: Any) -> QuestionCommandLookupResponse:
        calls.append(args)
        return _lookup_response(records, USER, EPOCH, "command-1", None)

    def command_lookup(*args: Any) -> QuestionCommandLookupResponse:
        calls.append(args)
        return _lookup_response(records, USER, EPOCH, "close-original", SESSION)

    monkeypatch.setattr(api, "close_question_session", close)
    monkeypatch.setattr(api, "read_question_start_command", start_lookup)
    monkeypatch.setattr(api, "read_question_command", command_lookup)
    result = client.post(f"/api/v1/finite-planning/sessions/{SESSION}/close", json=close_body())
    assert result.status_code == 200 and result.json()["effective_state"] == "CLOSED"
    assert (
        result.json()["fresh_evaluation_at"] is None and result.json()["pending_question"] is None
    )
    assert calls[-1][1:3] == (USER, SESSION) and calls[-1][-1] == NOW
    start = client.get(f"/api/v1/finite-planning/sessions/commands/{EPOCH}/by-start-key/command-1")
    assert start.status_code == 200 and start.json()["original_receipt"]["revision"] == 1
    assert start.json()["current_revision"]["state"] == "CLOSED"
    assert calls[-1][1:4] == (USER, EPOCH, "command-1") and calls[-1][-1] == NOW
    command = client.get(
        f"/api/v1/finite-planning/sessions/{SESSION}/commands/by-key/close-original"
    )
    assert command.status_code == 200 and command.json()["original_command"]["kind"] == "CLOSE"
    assert calls[-1][1:4] == (USER, SESSION, "close-original") and calls[-1][-1] == NOW


@pytest.mark.parametrize(
    "extra", ["user_id", "now", "accepted", "bank_authority", "effect_hash", "amount_cents"]
)
def test_close_rejects_financial_override_or_grant(client: TestClient, extra: str) -> None:
    assert (
        client.post(
            f"/api/v1/finite-planning/sessions/{SESSION}/close", json=close_body() | {extra: True}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("value", [True, "1", 0, 18])
def test_close_revision_stays_strict_and_bounded(client: TestClient, value: Any) -> None:
    assert (
        client.post(
            f"/api/v1/finite-planning/sessions/{SESSION}/close",
            json=close_body() | {"expected_revision": value},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "path",
    [
        "commands/bad-epoch/by-start-key/key",
        f"commands/{EPOCH}/by-start-key/bad key",
        f"{SESSION}/commands/by-key/bad key",
    ],
)
def test_key_lookup_rejects_malformed_identity_or_key(client: TestClient, path: str) -> None:
    assert client.get("/api/v1/finite-planning/sessions/" + path).status_code == 422


def test_all_new_gets_reject_query_override(client: TestClient) -> None:
    for path in (f"commands/{EPOCH}/by-start-key/key", f"{SESSION}/commands/by-key/key"):
        assert (
            client.get("/api/v1/finite-planning/sessions/" + path + "?result=success").status_code
            == 422
        )
