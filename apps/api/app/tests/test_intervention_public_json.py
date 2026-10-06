"""The real FastAPI JSON adapter must reach the service with strict typed identities."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1.full_intervention import router
from app.domain.full_intervention import (
    AcknowledgmentRequest,
    BoundaryObservationRequest,
    DeliveryRequest,
    QuestionObservationRequest,
)
from app.services import full_intervention as service
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_question_workflow import EPOCH, SESSION, revision
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.mark.parametrize("kind", ["QUESTION", "SINGLE_ACTION_BOUNDARY", "DELIVER", "ACKNOWLEDGE"])
def test_public_legal_json_identity_reaches_service_without_loosening_boolean_or_integer_fields(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_engine] = lambda: MagicMock()
    app.dependency_overrides[get_session] = lambda: MagicMock()
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=USER)
    app.dependency_overrides[get_now] = lambda: NOW
    call = MagicMock(
        side_effect=HTTPException(status_code=409, detail="TOOL_ONLY_TYPED_SERVICE_REACHED")
    )
    monkeypatch.setattr(service, "observe_intervention", call)
    monkeypatch.setattr(service, "deliver_intervention", call)
    monkeypatch.setattr(service, "acknowledge_intervention", call)
    request: QuestionObservationRequest | BoundaryObservationRequest | DeliveryRequest
    if kind == "QUESTION":
        request = QuestionObservationRequest(
            kind="QUESTION",
            session_id=SESSION,
            expected_revision=1,
            expected_run_id=revision().run_id,
            reviewed_source_trace_hash="a" * 64,
            expected_epoch_id=EPOCH,
            intervention_policy_id=UUID(int=124),
            idempotency_key="public-json-original",
        )
        url, index = "/api/v1/interventions/observe", 2
    elif kind == "SINGLE_ACTION_BOUNDARY":
        request = BoundaryObservationRequest(
            kind="SINGLE_ACTION_BOUNDARY",
            observation_run_id=revision().run_id,
            reviewed_source_trace_hash="a" * 64,
            expected_epoch_id=EPOCH,
            intervention_policy_id=None,
            idempotency_key="public-json-original",
        )
        url, index = "/api/v1/interventions/observe", 2
    elif kind == "DELIVER":
        request = DeliveryRequest(expected_epoch_id=EPOCH, reviewed_payload_hash="a" * 64)
        url, index = f"/api/v1/interventions/{UUID(int=123)}/deliveries", 3
    else:
        request = AcknowledgmentRequest(
            expected_epoch_id=EPOCH,
            reviewed_payload_hash="a" * 64,
            idempotency_key="public-json-original",
            acknowledged=True,
        )
        url, index = f"/api/v1/interventions/{UUID(int=123)}/acknowledgements", 3
    with TestClient(app) as client:
        response = client.post(url, json=request.model_dump(mode="json"))
        assert (
            response.status_code == 409
            and response.json()["detail"] == "TOOL_ONLY_TYPED_SERVICE_REACHED"
        ), response.text
        call.assert_called_once()
        received = call.call_args.args[index]
        assert received == request and isinstance(received.expected_epoch_id, UUID)
        call.reset_mock()
        for field, value in (
            ("expected_epoch_id", True),
            ("bank_result", True),
            ("amount_cents", 100),
        ):
            assert (
                client.post(url, json=request.model_dump(mode="json") | {field: value}).status_code
                == 422
            )
        if kind == "QUESTION":
            for invalid_revision in (True, "1", 1.0):
                assert (
                    client.post(
                        url,
                        json=request.model_dump(mode="json")
                        | {"expected_revision": invalid_revision},
                    ).status_code
                    == 422
                )
        if kind == "ACKNOWLEDGE":
            for invalid_acknowledgment in (1, "true", False):
                assert (
                    client.post(
                        url,
                        json=request.model_dump(mode="json")
                        | {"acknowledged": invalid_acknowledgment},
                    ).status_code
                    == 422
                )
        call.assert_not_called()
