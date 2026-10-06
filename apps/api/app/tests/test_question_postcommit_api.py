"""Real route calls with explicit synthetic committed receipts; no PG or human evidence."""

from types import SimpleNamespace
from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now
from app.api.v1 import question_workflow as api
from app.db.models import User
from app.services.question_intervention_producer import QuestionProducerResult
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_question_workflow import EPOCH, SESSION
from app.tests.test_question_workflow_contract import answer_body, response, start_body
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize("kind", ["START", "ANSWER", "REFRESH", "CLOSE"])
@pytest.mark.parametrize("failure", [False, True])
def test_postcommit_notification_never_replaces_original_receipt(
    kind: str, failure: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = FastAPI()
    app.include_router(api.router)
    engine = SimpleNamespace(synthetic_only=True)
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    original = response()
    order: list[str] = []

    def committed(*args: Any) -> Any:
        order.append("COMMITTED_QUESTION")
        return original

    def observe(*args: Any) -> QuestionProducerResult:
        assert order == ["COMMITTED_QUESTION"]
        assert args == (engine, USER, SESSION, NOW)
        order.append("OBSERVE")
        if failure:
            raise RuntimeError("SYNTHETIC_SECRET_MUST_NOT_REPLACE_ORIGINAL_RESPONSE")
        return QuestionProducerResult(user_id=USER, session_id=SESSION, status="NOT_PENDING")

    for service in ("start_question_session", "answer_question_session", "close_question_session"):
        monkeypatch.setattr(api, service, committed)
    monkeypatch.setattr(api, "produce_current_question_intervention", observe)
    path = "/api/v1/finite-planning/sessions"
    if kind == "START":
        body = start_body()
    elif kind == "ANSWER":
        path += f"/{SESSION}/answers"
        body = answer_body()
    else:
        path += f"/{SESSION}/{kind.lower()}"
        body = {
            "expected_epoch_id": str(EPOCH),
            "expected_revision": 1,
            "idempotency_key": "original-postcommit-key",
        }
    with TestClient(app) as client:
        result = client.post(path, json=body)
    assert result.status_code == 200
    assert result.json() == original.model_dump(mode="json")
    assert result.headers["X-Question-Intervention-Status"] == (
        "SOURCE_UNVERIFIED" if failure else "NOT_PENDING"
    )
    assert "SYNTHETIC_SECRET" not in result.text
    assert order == ["COMMITTED_QUESTION", "OBSERVE"]
