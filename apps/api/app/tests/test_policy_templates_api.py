"""Read-only FULL-103 routes mounted alone; no database or financial service is used."""

from copy import deepcopy
from typing import Any

import pytest
from app.api.v1.policy_templates import router
from app.domain.full_policy_configuration import TemplateName, template_schema
from app.domain.policy_configuration import configuration_hash
from app.tests.test_full_policy_configuration import EXAMPLES
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    api = FastAPI()
    api.include_router(router)
    return TestClient(api)


def test_catalog_preserves_twelve_names_and_five_exact_legacy_mappings(client: TestClient) -> None:
    response = client.get("/api/v1/policy-templates")
    assert response.status_code == 200
    body = response.json()
    assert len(body["templates"]) == 12
    assert {row["template_name"] for row in body["templates"]} == set(EXAMPLES)
    assert sum(row["mvp_configuration_type"] is not None for row in body["templates"]) == 5
    goal = next(row for row in body["templates"] if row["template_name"] == "LongTermGoalPolicy")
    assert goal["full_configuration_type"] == "long_term_goal"
    assert goal["mvp_configuration_type"] == "goal_saving"
    assert body["candidate_only"] is True and body["authority_granted"] is False


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_every_public_schema_matches_its_actual_domain_model_and_hash(
    client: TestClient, name: TemplateName
) -> None:
    response = client.get(f"/api/v1/policy-templates/{name}/schema")
    assert response.status_code == 200
    body = response.json()
    assert body["json_schema"] == template_schema(name, "FULL_V1")
    assert body["schema_sha256"] == configuration_hash(body["json_schema"])
    assert body["cross_field_validation_required"] is True
    assert body["candidate_only"] is True and body["authority_granted"] is False


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_public_candidate_validation_is_callable_without_any_db_dependency(
    client: TestClient, name: TemplateName
) -> None:
    response = client.post(
        "/api/v1/policy-templates/validate",
        json={"template_name": name, "configuration": EXAMPLES[name]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["configuration_hash"] == configuration_hash(body["normalized_configuration"])
    assert body["candidate_only"] is True
    assert body["authority_granted"] is False
    assert body["reference_validation_pending"] is True
    assert "policy_id" not in body and "status" not in body


@pytest.mark.parametrize(
    ("name", "version"),
    [("UnknownPolicy", "FULL_V1"), ("DatedExpensePolicy", "MVP_V1"), ("RecoveryPolicy", "FULL_V2")],
)
def test_unknown_or_unavailable_template_versions_reject_without_fallback(
    client: TestClient, name: str, version: str
) -> None:
    assert (
        client.get(
            f"/api/v1/policy-templates/{name}/schema", params={"dsl_version": version}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("extra", ["accepted", "user_id", "status", "grant", "observed_at"])
def test_candidate_request_cannot_supply_confirmation_owner_or_state(
    client: TestClient, extra: str
) -> None:
    response = client.post(
        "/api/v1/policy-templates/validate",
        json={
            "template_name": "InterventionPolicy",
            "configuration": {"type": "intervention"},
            extra: True,
        },
    )
    assert response.status_code == 422


def test_nested_cross_field_error_is_redacted_and_never_returns_a_hash(client: TestClient) -> None:
    candidate = deepcopy(EXAMPLES["PeriodicTransferPolicy"])
    candidate["payee_id"] = "PRIVATE_ORIGINAL_PAYEE"
    candidate["single_action_cap_cents"] = 1
    response = client.post(
        "/api/v1/policy-templates/validate",
        json={"template_name": "PeriodicTransferPolicy", "configuration": candidate},
    )
    assert response.status_code == 422
    assert "PRIVATE_ORIGINAL_PAYEE" not in response.text
    assert "configuration_hash" not in response.text


def test_mvp_goal_schema_and_hash_are_accessible_without_silent_full_conversion(
    client: TestClient,
) -> None:
    candidate: dict[str, Any] = {
        "type": "goal_saving",
        "target_cents": 3000000,
        "deadline": "2027-10-01",
        "monthly_contribution": {"min_cents": 180000, "target_cents": 200000, "max_cents": 250000},
    }
    response = client.post(
        "/api/v1/policy-templates/validate",
        json={
            "template_name": "LongTermGoalPolicy",
            "dsl_version": "MVP_V1",
            "configuration": candidate,
        },
    )
    assert response.status_code == 200
    assert response.json()["normalized_configuration"]["type"] == "goal_saving"
    assert (
        client.post(
            "/api/v1/policy-templates/validate",
            json={"template_name": "LongTermGoalPolicy", "configuration": candidate},
        ).status_code
        == 422
    )


def test_openapi_exposes_only_the_three_readonly_candidate_operations(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert {path: set(methods) for path, methods in paths.items()} == {
        "/api/v1/policy-templates": {"get"},
        "/api/v1/policy-templates/{template_name}/schema": {"get"},
        "/api/v1/policy-templates/validate": {"post"},
    }
