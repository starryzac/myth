"""Synthetic HTTP boundary only, not actual PG or signed banking evidence."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid5

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import full_action_set_boundary_recovery_composed as api
from app.domain.full_action_set_boundary import GlobalBoundaryObserveRequest
from app.domain.full_action_set_boundary_recovery_composed import (
    RecoveryComposedGlobalObservation,
    derive_recovery_composed_action_set,
)
from app.domain.policy_configuration import configuration_hash
from app.services.full_action_set_recovery_observations import NAMESPACE
from app.tests.test_full_action_set_boundary_recovery_composed import fixture
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_get_uses_only_server_owner_and_clock_and_rejects_query_and_money(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = fixture()
    base = data.recovery.original_actual_input.base
    result = derive_recovery_composed_action_set(data)
    read = MagicMock(return_value=result)
    monkeypatch.setattr(api, "read_current_recovery_composed_action_set", read)
    app = FastAPI()
    app.include_router(api.router)
    session = MagicMock()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=base.user_id)
    app.dependency_overrides[get_now] = lambda: base.as_of
    with TestClient(app) as client:
        path = "/api/v1/boundary/recovery-composed-action-set/current"
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == result.model_dump(mode="json")
        read.assert_called_once_with(session, base.user_id, base.as_of)
        assert client.get(path + "?amount_cents=50000").status_code == 422
        assert client.post(path, json={"bank_authority": True}).status_code == 405
        assert read.call_count == 1
    session.commit.assert_not_called()
    session.flush.assert_not_called()


def test_observe_only_accepts_original_identity_and_reads_server_owned_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = fixture()
    snapshot = derive_recovery_composed_action_set(data)
    body = GlobalBoundaryObserveRequest(
        expected_epoch_id=snapshot.epoch_id,
        idempotency_key="synthetic-http-recovery-observation",
        previous_observation_run_id=None,
    )
    run_id = uuid5(NAMESPACE, f"{snapshot.user_id}:{snapshot.epoch_id}:{body.idempotency_key}")
    value = RecoveryComposedGlobalObservation(
        user_id=snapshot.user_id,
        epoch_id=snapshot.epoch_id,
        observation_run_id=run_id,
        previous_observation_run_id=None,
        original_request=body,
        request_hash=configuration_hash(body.model_dump(mode="json")),
        snapshot=snapshot,
        kind=None,
        semantic_key=None,
        requires_user_attention=False,
        previous_snapshot_hash=None,
        previous_action_set_signature=None,
        global_action_set_complete=False,
    )
    record, read, engine, session = (
        MagicMock(return_value=value),
        MagicMock(return_value=value),
        MagicMock(),
        MagicMock(),
    )
    monkeypatch.setattr(api, "observe_recovery_composed_action_set", record)
    monkeypatch.setattr(api, "read_recovery_composed_observation", read)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=snapshot.user_id)
    app.dependency_overrides[get_now] = lambda: snapshot.as_of
    with TestClient(app) as client:
        path = "/api/v1/boundary/recovery-composed-action-set"
        payload = body.model_dump(mode="json")
        response = client.post(path + "/observe", json=payload)
        assert response.status_code == 200 and response.json() == value.model_dump(mode="json")
        record.assert_called_once_with(engine, snapshot.user_id, body, snapshot.as_of)
        for field, extra in (
            ("amount_cents", 1),
            ("user_id", str(snapshot.user_id)),
            ("bank_authority", True),
            ("kind", "BoundaryCrossed"),
        ):
            assert client.post(path + "/observe", json={**payload, field: extra}).status_code == 422
        assert client.post(path + "/observe?amount_cents=1", json=payload).status_code == 422
        response = client.get(path + f"/observations/{run_id}")
        assert response.status_code == 200 and response.json() == value.model_dump(mode="json")
        read.assert_called_once_with(session, snapshot.user_id, run_id, snapshot.as_of)
        assert (
            client.get(path + f"/observations/{run_id}?user_id={snapshot.user_id}").status_code
            == 422
        )
        assert record.call_count == 1 and read.call_count == 1
