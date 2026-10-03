"""Lifecycle HTTP contracts backed by real PostgreSQL and a trusted test clock."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.api.dependencies import get_engine
from app.db.models import Policy, PolicyProposal, User
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.demo_identity import DEMO_USER_ID, DEMO_USER_REF
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)
RENT: dict[str, Any] = {
    "type": "recurring_obligation",
    "name": "房租",
    "payee_id": "landlord_demo",
    "amount_rule": {"kind": "range", "min_cents": 175_000, "max_cents": 185_000},
    "due_day": 28,
    "prepare_days_before": 3,
    "auto_execute": True,
    "valid_from": "2026-10-01",
    "valid_until": "2027-06-30",
    "priority": {
        "importance": 100,
        "minimum_cents": 175_000,
        "reducible": False,
        "deferrable": False,
    },
}


@pytest.fixture
def policy_client() -> Iterator[tuple[TestClient, dict[str, Any]]]:
    from app.api.dependencies import get_now

    with temporary_database() as url:
        config = Config(str(Path(__file__).resolve().parents[4] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        proposal_id, other_proposal_id, other_user_id = uuid4(), uuid4(), uuid4()
        other_policy_id = uuid4()
        with Session(engine) as session, session.begin():
            session.add_all(
                [
                    User(id=DEMO_USER_ID, external_ref=DEMO_USER_REF, display_name="演示"),
                    User(id=other_user_id, external_ref="other-policy-user", display_name="Other"),
                ]
            )
            session.flush()
            session.add(
                Policy(
                    id=other_policy_id,
                    user_id=other_user_id,
                    name="Other policy",
                    policy_type="recurring_obligation",
                    status="PROPOSED",
                )
            )
            for user_id, item_id in [
                (DEMO_USER_ID, proposal_id),
                (other_user_id, other_proposal_id),
            ]:
                session.add(
                    PolicyProposal(
                        id=item_id,
                        user_id=user_id,
                        source_type="USER_DECLARED",
                        source_text="每月房租",
                        compiler_version="test-fixture-v1",
                        proposed_configuration=RENT,
                        evidence_ids=[],
                        idempotency_key="rent-proposal",
                    )
                )
        state: dict[str, Any] = {
            "now": NOW,
            "proposal_id": str(proposal_id),
            "other_proposal_id": str(other_proposal_id),
            "other_policy_id": str(other_policy_id),
        }
        api = create_app()
        api.dependency_overrides[get_engine] = lambda: engine
        api.dependency_overrides[get_now] = lambda: state["now"]
        try:
            with TestClient(api) as client:
                yield client, state
        finally:
            engine.dispose()


def confirm_first(client: TestClient, state: dict[str, Any]) -> dict[str, Any]:
    response = client.get("/api/v1/policy-proposals")
    assert response.status_code == 200
    proposals = response.json()["items"]
    assert len(proposals) == 1
    assert proposals[0]["validation_ready"] is True
    body = {"accepted": True, "reviewed_hash": proposals[0]["configuration_hash"]}
    response = client.post(f"/api/v1/policy-proposals/{state['proposal_id']}/confirm", json=body)
    assert response.status_code == 200
    first: dict[str, Any] = response.json()
    assert first["status"] == first["effective_status"] == "ACTIVE"
    assert (
        client.post(f"/api/v1/policy-proposals/{state['proposal_id']}/confirm", json=body).json()[
            "current_version_id"
        ]
        == first["current_version_id"]
    )
    return first


def test_confirm_patch_retry_suspend_and_revoke_preserve_version_history(
    policy_client: tuple[TestClient, dict[str, Any]],
) -> None:
    from app.domain.policy_configuration import configuration_hash, validate_configuration

    client, state = policy_client
    first = confirm_first(client, state)
    assert client.get("/api/v1/policies").json()["items"][0]["version_authorized"] is True
    path = f"/api/v1/policies/{first['policy_id']}"
    initial = client.get(path + "/versions").json()["items"]
    assert len(initial) == 1
    configuration = validate_configuration(
        {**RENT, "amount_rule": {"kind": "range", "min_cents": 180_000, "max_cents": 200_000}}
    )
    patch = {
        "expected_version_id": first["current_version_id"],
        "configuration": configuration,
        "accepted": True,
        "reviewed_hash": configuration_hash(configuration),
        "reason": "房租范围变更",
        "idempotency_key": "change-rent-api-1",
    }
    response = client.patch(path, json=patch)
    assert response.status_code == 200
    second = response.json()
    assert second["current_version_id"] != first["current_version_id"]
    assert (
        client.patch(path, json=patch).json()["current_version_id"] == second["current_version_id"]
    )
    stale = client.patch(path, json={**patch, "idempotency_key": "different-attempt"})
    assert stale.status_code == 409
    history = client.get(path + "/versions").json()["items"]
    assert len(history) == 2
    assert history[0] == initial[0]
    assert history[1]["previous_hash"] == history[0]["content_hash"]
    expected = {"expected_version_id": second["current_version_id"]}
    assert client.post(path + "/suspend", json=expected).json()["status"] == "SUSPENDED"
    assert client.post(path + "/suspend", json=expected).json()["status"] == "SUSPENDED"
    assert client.post(path + "/revoke", json=expected).json()["status"] == "REVOKED"
    assert client.post(path + "/revoke", json=expected).json()["status"] == "REVOKED"
    policies = client.get("/api/v1/policies").json()["items"]
    assert policies[0]["effective_status"] == "REVOKED"
    assert policies[0]["version_authorized"] is False
    assert len(client.get(path + "/versions").json()["items"]) == 2


def test_unreviewed_or_injected_authority_is_rejected(
    policy_client: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = policy_client
    proposal = client.get("/api/v1/policy-proposals").json()["items"][0]
    path = f"/api/v1/policy-proposals/{state['proposal_id']}/confirm"
    valid = {"accepted": True, "reviewed_hash": proposal["configuration_hash"]}
    for invalid in [
        {**valid, "accepted": False},
        {**valid, "accepted": 1},
        {**valid, "as_of": "2026-01-01"},
        {**valid, "status": "ACTIVE"},
    ]:
        response = client.post(path, json=invalid)
        assert response.status_code == 422
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    response = client.post(path, json={**valid, "reviewed_hash": "0" * 64})
    assert response.status_code == 409
    assert client.get("/api/v1/policies").json()["items"] == []


def test_other_user_proposal_and_unknown_policy_are_not_exposed(
    policy_client: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = policy_client
    assert (
        client.post(
            f"/api/v1/policy-proposals/{state['other_proposal_id']}/confirm",
            json={"accepted": True, "reviewed_hash": "0" * 64},
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/policies/{UUID(int=1)}/versions").status_code == 404
    other_path = f"/api/v1/policies/{state['other_policy_id']}"
    assert client.get(other_path + "/versions").status_code == 404
    expected = {"expected_version_id": str(UUID(int=1))}
    for action in ("suspend", "revoke"):
        assert client.post(other_path + "/" + action, json=expected).status_code == 404
    proposal = client.get("/api/v1/policy-proposals").json()["items"][0]
    response = client.patch(
        other_path,
        json={
            **expected,
            "configuration": proposal["configuration"],
            "accepted": True,
            "reviewed_hash": proposal["configuration_hash"],
            "reason": "Must not change another user",
            "idempotency_key": "other-user-attempt",
        },
    )
    assert response.status_code == 404


def test_read_effective_expiry_without_relying_on_a_background_scan(
    policy_client: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = policy_client
    confirm_first(client, state)
    state["now"] = datetime(2027, 6, 30, 16, tzinfo=UTC)
    response = client.get("/api/v1/policies")
    assert response.status_code == 200
    assert response.json()["items"][0]["effective_status"] == "EXPIRED"
    assert response.json()["items"][0]["version_authorized"] is False
