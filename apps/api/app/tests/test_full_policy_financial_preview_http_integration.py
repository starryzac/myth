"""Actual HTTP preview/change comparison candidate; root runs the isolated money chain."""

from copy import deepcopy
from uuid import UUID

import pytest
from app.db.models import Account
from app.services.full_policy_lifecycle import canonical_candidate
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_full_protection_projection_api import confirm
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_financial_preview_http_is_readonly_and_future_change_matches_original_curve(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    config = {
        "type": "dated_expense",
        "name": "隔离 HTTP 预览确认一致性原支出",
        "window": {"start": "2026-10-12", "end": "2026-10-15"},
        "amount": {"min_cents": 0, "target_cents": 100, "max_cents": 200},
    }
    registered = confirm(client, "DatedExpensePolicy", config, "root-impact-http-create")
    policy_id, original_version = registered["policy_id"], registered["version_id"]
    candidate = {**config, "amount": {"min_cents": 0, "target_cents": 150, "max_cents": 350}}
    body = {"expected_version_id": original_version, "configuration": candidate}
    path = f"/api/v1/full-policies/{policy_id}/financial-change-preview"
    before = physical_snapshot(engine)
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["hypothetical"] is True and value["grants_authority"] is False
    assert value["after_configuration"] == canonical_candidate("DatedExpensePolicy", candidate)
    impact = value["financial_impact"]
    assert impact["status"] == "PROJECTED" and impact["delta_minimum_margin_cents"] == -150
    assert len(impact["after"]["calculation_trace"]) == 1098
    assert client.post(path, json=body).json() == value
    for field in ("user_id", "now", "amount_cents", "result", "authority"):
        assert client.post(path, json={**body, field: 1}).status_code == 422
    assert client.post(path + "?owner=client", json=body).status_code == 422
    assert physical_snapshot(engine) == before
    changed = client.post(
        f"/api/v1/full-policies/{policy_id}/change",
        json={
            **body,
            "reviewed_hash": value["configuration_hash"],
            "accepted": True,
            "reason": "隔离实际用户明确确认未来支出差量",
            "idempotency_key": "root-impact-http-change",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["version_id"] != original_version
    actual = client.get("/api/v1/planning/full-annual")
    assert actual.status_code == 200, actual.text
    annual = actual.json()["projection"]["full_annual_projection"]
    assert annual is not None, actual.text
    # A hypothesis has an unconfirmed configuration identity; confirmation
    # creates an actual version. Bind that exact identity transition,
    # then require every numerical field and all other occurrence IDs to match.
    actual_version = changed.json()["version_id"]
    old_occurrence = f"CANDIDATE:{policy_id}:{value['configuration_hash']}:2026-10-12"
    new_occurrence = f"FULL:{policy_id}:{actual_version}:DATED:2026-10-12:2026-10-15"
    expected_trace = deepcopy(impact["after"]["calculation_trace"])
    assert any(old_occurrence in row["obligation_occurrence_ids"] for row in expected_trace)
    for point in expected_trace:
        point["obligation_occurrence_ids"] = sorted(
            new_occurrence if identity == old_occurrence else identity
            for identity in point["obligation_occurrence_ids"]
        )
    assert annual["calculation_trace"] == expected_trace
    assert actual.json()["projection"]["algorithm_version"] == (
        "registered-full-protection-future-dated-history-v2"
    )
    assert annual["algorithm_version"] == actual.json()["projection"]["algorithm_version"]
    assert annual["boundary_hash"] != impact["after"]["curve_hash"]
    actual_occurrence = actual.json()["projection"]["occurrences"][0]
    assert actual_occurrence["policy_version_id"] == actual_version
    assert actual_occurrence["occurrence_id"] == new_occurrence
    for name in ("minimum_margin_cents", "safe_idle_cents", "max_allocatable_by_product"):
        assert annual[name] == impact["after"][name]
    # This is a same-server-clock future-only Dated positive. Prior unsettled
    # versions and the other templates do not gain preview support from it.
    after_change = physical_snapshot(engine)
    stale = client.post(path, json=body)
    assert stale.status_code == 409
    assert physical_snapshot(engine) == after_change
    current_body = {**body, "expected_version_id": changed.json()["version_id"]}
    current = client.post(path, json=current_body)
    assert current.status_code == 200, current.text
    assert current.json()["financial_impact"]["status"] == "UNKNOWN"
    assert current.json()["financial_impact"]["after"] is None
    assert (
        "CURRENT_OR_PRIOR_VERSION_UNPAID_NOT_PROVEN"
        in current.json()["financial_impact"]["reasons"]
    )
    assert physical_snapshot(engine) == after_change
    with Session(engine) as session, session.begin():
        account = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert account is not None
        account.balance_cents += 1
    tampered = physical_snapshot(engine)
    rejected = client.post(path, json=current_body)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["financial_impact"]["status"] == "UNKNOWN"
    assert rejected.json()["financial_impact"]["after"] is None
    assert rejected.json()["financial_impact"]["delta_safe_idle_cents"] is None
    assert physical_snapshot(engine) == tampered
    assert UUID(changed.json()["policy_id"]) == UUID(policy_id)
