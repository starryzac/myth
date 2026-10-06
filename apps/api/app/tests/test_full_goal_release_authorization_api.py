"""Real JSON parsing; the explicit service double is not a bank or persistence proof."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import full_goal_release_authorization as api
from app.domain.full_goal_release_authorization import ReleaseAuthorizationPreviewRequest
from app.tests.test_full_goal_release_authorization import NOW, original
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.mark.parametrize("operation", ["preview", "confirm"])
def test_dedicated_consent_json_reaches_typed_service_and_rejects_client_financial_facts(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant = original()
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: MagicMock()
    app.dependency_overrides[get_engine] = lambda: MagicMock()
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=grant.user_id)
    app.dependency_overrides[get_now] = lambda: NOW
    call = MagicMock(
        side_effect=HTTPException(status_code=409, detail="TOOL_ONLY_TYPED_SCOPE_REACHED")
    )
    monkeypatch.setattr(api, "preview_release_authorization", call)
    monkeypatch.setattr(api, "confirm_release_authorization", call)
    body = (
        grant.original_request
        if operation == "confirm"
        else ReleaseAuthorizationPreviewRequest(
            expected_epoch_id=grant.epoch_id, expected_policy_version_id=grant.policy_version_id
        )
    )
    url = f"/api/v1/goal-release-authorizations/policies/{grant.policy_id}/{operation}"
    with TestClient(app) as client:
        response = client.post(url, json=body.model_dump(mode="json"))
        assert (
            response.status_code == 409
            and response.json()["detail"] == "TOOL_ONLY_TYPED_SCOPE_REACHED"
        )
        assert call.call_args.args[3] == body
        call.reset_mock()
        for field in ("amount_cents", "used_total_cents", "receipt", "grant", "now", "user_id"):
            assert (
                client.post(url, json=body.model_dump(mode="json") | {field: 10}).status_code == 422
            )
        assert (
            client.post(url + "?accepted=true", json=body.model_dump(mode="json")).status_code
            == 422
        )
        if operation == "confirm":
            for value in (1, False, "true"):
                assert (
                    client.post(
                        url, json=body.model_dump(mode="json") | {"accepted": value}
                    ).status_code
                    == 422
                )
        call.assert_not_called()
