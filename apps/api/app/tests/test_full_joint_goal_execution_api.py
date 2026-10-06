"""Synthetic signed USER/JSON risks; no SQL or financial runtime evidence."""

import json
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import full_joint_goal_execution as api
from app.api.v1.local_actor_sessions import get_local_actor_principal
from app.db.models import User
from app.domain.full_joint_goal_execution import (
    FullJointGoalConfirmRequest,
    FullJointGoalExecuteRequest,
    FullJointGoalPrepareRequest,
    FullJointWholeConsentContent,
)
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import configuration_hash
from app.services import full_joint_goal_execution_dispatch as service
from app.tests.test_full_dynamic_goal_execution import EPOCH, NOW, USER
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.engine import Engine

PLAN = UUID(int=678)


def principal() -> LocalActorPrincipal:
    return LocalActorPrincipal(
        user_id=USER,
        role="USER",
        session_id=UUID(int=679),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )


def setup() -> tuple[FastAPI, object, LocalActorPrincipal]:
    app, engine, actor = FastAPI(), object(), principal()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_local_actor_principal] = lambda: actor
    return app, engine, actor


def body_and_path(kind: str) -> tuple[dict[str, Any], str, str]:
    if kind == "prepare":
        return (
            {
                "full_policy_id": str(UUID(int=680)),
                "expected_full_policy_version_id": str(UUID(int=681)),
                "expected_epoch_id": str(EPOCH),
                "idempotency_key": "whole-original-key",
            },
            "/api/v1/joint-goal-actions/prepare",
            "prepare_full_joint_goal_execution",
        )
    body: dict[str, Any] = {
        "accepted": True,
        "reviewed_plan_hash": "a" * 64,
        "expected_epoch_id": str(EPOCH),
    }
    if kind == "confirm":
        body["idempotency_key"] = "whole-confirm-key"
        return (
            body,
            f"/api/v1/joint-goal-actions/{PLAN}/confirm",
            "confirm_full_joint_goal_execution",
        )
    body.update(expected_child_number=1, expected_action_id=str(UUID(int=682)))
    return body, f"/api/v1/joint-goal-actions/{PLAN}/execute-child", "execute_full_joint_goal_child"


@pytest.mark.parametrize("kind", ["prepare", "confirm", "execute-child"])
def test_json_write_identity_exact_signed_principal_and_no_client_facts(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    app, engine, actor = setup()
    body, path, name = body_and_path(kind)
    calls: list[tuple[Any, ...]] = []

    def sentinel(*args: Any) -> None:
        calls.append(args)
        raise HTTPException(409, "SYNTHETIC_PROTOCOL_SENTINEL_NOT_A_BANK_RESULT")

    monkeypatch.setattr(api, name, sentinel)
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 409
        for field in ("amount", "facts", "clock", "role", "actor", "authority", "result"):
            assert client.post(path, json=body | {field: 1}).status_code == 422
        assert client.post(path, json=body, params={"now": "fake"}).status_code == 422
        if kind != "prepare":
            for accepted in (False, 1, "true"):
                assert client.post(path, json=body | {"accepted": accepted}).status_code == 422
    assert len(calls) == 1 and calls[0][:2] == (engine, USER)
    assert calls[0][-3].model_dump(mode="json") == body
    assert calls[0][-2:] == (actor, NOW)


@pytest.mark.parametrize("change", ["no_cookie", "AGENT", "expired", "owner"])
@pytest.mark.parametrize("kind", ["prepare", "confirm", "execute-child"])
def test_missing_or_invalid_signed_user_never_calls_public_write(
    monkeypatch: pytest.MonkeyPatch, change: str, kind: str
) -> None:
    app, _, actor = setup()
    if change == "no_cookie":
        del app.dependency_overrides[get_local_actor_principal]
    else:
        updates: dict[str, Any] = (
            {"role": "AGENT"}
            if change == "AGENT"
            else {"user_id": UUID(int=999)}
            if change == "owner"
            else {"issued_at": NOW - timedelta(minutes=12), "expires_at": NOW}
        )
        app.dependency_overrides[get_local_actor_principal] = lambda: actor.model_copy(
            update=updates
        )
    body, path, name = body_and_path(kind)

    def forbidden(*args: Any) -> None:
        raise AssertionError("Untrusted identity reached a writing service")

    monkeypatch.setattr(api, name, forbidden)
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == (401 if change == "no_cookie" else 403)


@pytest.mark.parametrize("kind", ["prepare", "confirm", "execute-child"])
@pytest.mark.parametrize("change", ["missing", "AGENT", "expired", "owner"])
def test_actual_service_identity_gate_precedes_hooks_and_sql(kind: str, change: str) -> None:
    actor = principal()
    bad = (
        cast(LocalActorPrincipal, None)
        if change == "missing"
        else actor.model_copy(
            update={"role": "AGENT"}
            if change == "AGENT"
            else {"user_id": UUID(int=999)}
            if change == "owner"
            else {"issued_at": NOW - timedelta(minutes=12), "expires_at": NOW}
        )
    )
    raw, _, _ = body_and_path(kind)
    engine = cast(Engine, object())  # Any attempt to open SQL would fail this test.
    with pytest.raises(Exception) as caught:
        if kind == "prepare":
            service.prepare_full_joint_goal_execution(
                engine,
                USER,
                FullJointGoalPrepareRequest.model_validate_json(json.dumps(raw)),
                bad,
                NOW,
            )
        elif kind == "confirm":
            service.confirm_full_joint_goal_execution(
                engine,
                USER,
                PLAN,
                FullJointGoalConfirmRequest.model_validate_json(json.dumps(raw)),
                bad,
                NOW,
            )
        else:
            service.execute_full_joint_goal_child(
                engine,
                USER,
                PLAN,
                FullJointGoalExecuteRequest.model_validate_json(json.dumps(raw)),
                bad,
                NOW,
            )
    assert getattr(caught.value, "code", None) == "JOINT_CURRENT_SIGNED_USER_REQUIRED"


def test_closed_whole_consent_binds_actual_session_without_human_claim() -> None:
    raw, _, _ = body_and_path("confirm")
    body = FullJointGoalConfirmRequest.model_validate_json(json.dumps(raw))
    actor = principal()
    content = FullJointWholeConsentContent(
        user_id=USER,
        plan_id=PLAN,
        epoch_id=EPOCH,
        plan_hash="a" * 64,
        original_request=body,
        request_hash=configuration_hash(raw),
        confirmed_at=NOW,
        valid_until=NOW + timedelta(minutes=5),
        actor=actor,
        actor_session_id=actor.session_id,
    )
    assert content.actor.role == "USER" and content.human_identity_verified is False
    encoded = content.model_dump(mode="json")
    for change in (
        {"actor_session_id": str(UUID(int=999))},
        {"request_hash": "b" * 64},
        {"human_identity_verified": True},
        {"actor": actor.model_copy(update={"role": "AGENT"}).model_dump(mode="json")},
        {"invented_cause": "SUCCESS"},
    ):
        with pytest.raises(ValidationError):
            FullJointWholeConsentContent.model_validate_json(json.dumps(encoded | change))
