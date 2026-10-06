"""One actual isolated bank consumer risk candidate; Root alone schedules PG."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_now
from app.api.v1 import full_asset_execution as execution_router
from app.db.models import (
    Account,
    ActionPlan,
    AuditEpoch,
    BankOperation,
    EvidenceItem,
    PolicyVersion,
)
from app.domain.execution_types import BankCommand
from app.domain.policy_configuration import configuration_hash
from app.services import execution as original_execution
from app.services.audit_chain import verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID
from app.services.execution_bank import has_full_asset_batch_binding, process_operation
from app.services.full_asset_execution_dispatch import (
    enforce_full_asset_batch_acceptance,
    enforce_full_asset_batch_phase1,
    prepare_full_asset_execution,
)
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_full_asset_allocation_api import actual_asset_declaration, actual_income
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_whole_asset_portfolio_keeps_order_and_recovers_original_unknown_once(
    annual_client: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine = annual_client
    # Metadata admission is bound to the real OPEN epoch, unlike the older
    # annual read fixture's historical projection clock. Freeze one trusted
    # server clock after the real seed has opened its epoch; never submit it as
    # client money/clock input or modify that original seed/epoch/history.
    now = datetime.now(UTC)
    assert isinstance(client.app, FastAPI)
    with Session(engine) as session:
        open_epochs = list(
            session.scalars(
                select(AuditEpoch).where(
                    AuditEpoch.user_id == DEMO_USER_ID, AuditEpoch.status == "OPEN"
                )
            )
        )
        assert len(open_epochs) == 1
        assert now >= open_epochs[0].opened_at
    client.app.dependency_overrides[get_now] = lambda: now
    # The public error middleware returns INTERNAL_ERROR even when TestClient's
    # raise_server_exceptions is True. Retain the original exception for this
    # real service diagnostic; the wrapper never changes financial behavior.
    original_prepare = prepare_full_asset_execution
    prepare_errors: list[Exception] = []

    def diagnose_prepare(*args: Any, **kwargs: Any) -> Any:
        try:
            return original_prepare(*args, **kwargs)
        except Exception as cause:
            prepare_errors.append(cause)
            raise

    monkeypatch.setattr(execution_router, "prepare_full_asset_execution", diagnose_prepare)
    actual_income(engine)
    full_id = actual_asset_declaration(client, engine)
    epoch_id = client.get("/api/v1/demo/state").json()["epoch_id"]
    candidate = client.post(
        "/api/v1/policy-declarations",
        json={
            "configuration": authorization(max_auto_managed_cents=2000000),
            "idempotency_key": "whole-asset-original-MVP-permission",
            "expected_epoch_id": epoch_id,
            "source_proposal_id": None,
        },
    )
    assert candidate.status_code == 200, candidate.text
    declared = candidate.json()
    confirmed = client.post(
        f"/api/v1/policy-proposals/{declared['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": declared["configuration_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    mvp_id = confirmed.json()["policy_id"]
    with Session(engine) as session:
        version = session.scalar(
            select(PolicyVersion)
            .where(PolicyVersion.policy_id == UUID(mvp_id))
            .order_by(PolicyVersion.version_number.desc())
        )
        assert version is not None
        mvp_version = str(version.id)
        cash_before = {a.id: a.balance_cents for a in session.scalars(select(Account))}
    full_view = client.get(f"/api/v1/full-policies/{full_id}").json()
    request: dict[str, Any] = {
        "full_policy_id": full_id,
        "expected_full_policy_version_id": full_view["current_version"]["version_id"],
        "mvp_asset_policy_id": mvp_id,
        "expected_mvp_policy_version_id": mvp_version,
        "goal_id": None,
        "expected_goal_policy_version_id": None,
        "expected_epoch_id": epoch_id,
        "idempotency_key": "whole-real-original:1",
        "planning_mode": "PORTFOLIO",
    }
    base = "/api/v1/full-asset-executions"
    untouched = physical_snapshot(engine)
    preview = client.post(base + "/preview", json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()["state"] == "READY_TO_REVIEW", preview.text
    assert physical_snapshot(engine) == untouched
    prepared = client.post(base + "/prepare", json=request)
    if prepare_errors:
        raise prepare_errors[0]
    assert prepared.status_code == 200, prepared.text
    current = prepared.json()
    portfolio = current["original_portfolio"]
    assert datetime.fromisoformat(portfolio["prepared_at"]) == now
    assert now < datetime.fromisoformat(portfolio["expires_at"]) <= now + timedelta(minutes=15)
    assert len(portfolio["batches"]) >= 2
    assert current["state"] == "PREPARED_UNRESERVED"
    assert not current["bank_authority"] and not current["funds_reserved"]
    assert all(b["original_action"]["autonomy_level"] == "ASK_ONCE" for b in current["batches"])
    with Session(engine) as session:
        assert cash_before == {a.id: a.balance_cents for a in session.scalars(select(Account))}
        assert list(session.scalars(select(BankOperation))) == []
    parent_id, parent_hash = portfolio["portfolio_id"], portfolio["portfolio_hash"]
    url = f"{base}/portfolios/{parent_id}"
    execute_body = {
        "accepted": True,
        "reviewed_portfolio_hash": parent_hash,
        "expected_epoch_id": epoch_id,
        "expected_batch_number": 1,
        "expected_action_id": portfolio["batches"][0]["action_id"],
    }
    assert client.post(url + "/execute-next", json=execute_body).status_code == 409
    confirm_body = {
        "accepted": True,
        "reviewed_portfolio_hash": parent_hash,
        "expected_epoch_id": epoch_id,
        "idempotency_key": "whole-real-confirm:1",
    }
    assert (
        client.post(
            url + "/confirm", json=confirm_body | {"idempotency_key": request["idempotency_key"]}
        ).status_code
        == 409
    )
    absent = client.get(f"{base}/commands/{epoch_id}/by-key/whole-not-recorded")
    assert absent.status_code == 200 and absent.json()["status"] == "NOT_FOUND_NOT_FINAL"
    assert absent.json()["command_kind"] is None and absent.json()["original_request"] is None
    assert not absent.json()["not_found_is_final"] and not absent.json()["replacement_allowed"]
    registered = physical_snapshot(engine)
    with Session(engine) as session:
        actual = session.get(ActionPlan, UUID(portfolio["batches"][0]["action_id"]))
        assert actual is not None
        stripped = ActionPlan(
            id=actual.id,
            user_id=actual.user_id,
            action_type=actual.action_type,
            idempotency_key="action:" + "f" * 64,
            request={
                key: value for key, value in actual.request.items() if key != "full_asset_execution"
            },
        )
        stripped.request_hash = configuration_hash(stripped.request)
        assert has_full_asset_batch_binding(session, stripped)
        command = BankCommand.model_validate_json(json.dumps(portfolio["batches"][0]["command"]))
        for guard in (enforce_full_asset_batch_phase1, enforce_full_asset_batch_acceptance):
            with pytest.raises(ValueError, match="marker缺失"):
                guard(engine, session, stripped, command, now)
    assert physical_snapshot(engine) == registered
    ack = client.post(url + "/confirm", json=confirm_body)
    assert ack.status_code == 200, ack.text
    assert ack.json()["state"] == "CONFIRMED_UNRESERVED"
    assert ack.json()["original_consent_verified"]
    assert ack.json()["original_consent"]["original_request"] == confirm_body
    assert ack.json()["original_consent"]["current_evidence_verified"]
    consent_lookup = client.get(
        f"{base}/commands/{epoch_id}/by-key/{confirm_body['idempotency_key']}"
    )
    assert consent_lookup.status_code == 200, consent_lookup.text
    assert (
        consent_lookup.json()["simulation"] and consent_lookup.json()["command_kind"] == "CONFIRM"
    )
    assert consent_lookup.json()["original_request"] == confirm_body
    assert consent_lookup.json()["request_hash"] == configuration_hash(confirm_body)
    unchanged = physical_snapshot(engine)
    assert client.post(url + "/confirm", json=confirm_body).json() == ack.json()
    assert client.post(base + "/prepare", json=request).json()["original_portfolio"] == portfolio
    assert physical_snapshot(engine) == unchanged
    second_action = portfolio["batches"][1]["action_id"]
    refused = client.post(f"/api/v1/actions/{second_action}/execute", json={})
    assert refused.status_code == 409, refused.text
    assert physical_snapshot(engine) == unchanged
    original_process = process_operation
    lost_errors: list[TimeoutError] = []

    def lose_response(*args: Any, **kwargs: Any) -> Any:
        original_process(*args, **kwargs)
        lost = TimeoutError("actual bank committed; response deliberately lost")
        lost_errors.append(lost)
        raise lost

    with monkeypatch.context() as fault:
        fault.setattr(original_execution, "process_operation", lose_response)
        with pytest.raises(TimeoutError):
            lost_response = client.post(url + "/execute-next", json=execute_body)
            assert lost_response.status_code == 500, lost_response.text
            assert len(lost_errors) == 1
            # Preserve the real injected exception despite public middleware
            # returning its safe envelope; bank assertions below remain exact.
            raise lost_errors[0]
    uncertain = client.get(url)
    assert uncertain.status_code == 200, uncertain.text
    raw = uncertain.json()
    assert raw["state"] == "UNRESOLVED"
    assert raw["batches"][0]["original_action"]["bank_status"] == "SETTLED"
    assert raw["batches"][0]["original_action"]["receipt"] is None
    with Session(engine) as session:
        operation = session.scalar(select(BankOperation))
        assert operation is not None and operation.status == "SETTLED"
        assert operation.idempotency_key == portfolio["batches"][0]["bank_idempotency_key"]
        assert operation.request == portfolio["batches"][0]["command"]
        assert operation.request_hash == configuration_hash(operation.request)
    bank_unknown = physical_snapshot(engine)
    assert client.post(f"/api/v1/actions/{second_action}/execute", json={}).status_code == 409
    assert physical_snapshot(engine) == bank_unknown
    recovered = client.post(url + "/execute-next", json=execute_body)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["batches"][0]["original_action"]["receipt"] is not None
    first_settled_rows = physical_snapshot(engine)
    assert client.post(url + "/execute-next", json=execute_body).json() == recovered.json()
    assert physical_snapshot(engine) == first_settled_rows
    assert recovered.json()["batches"][1]["original_action"]["receipt"] is None
    for batch in portfolio["batches"][1:]:
        next_body = execute_body | {
            "expected_batch_number": batch["batch_number"],
            "expected_action_id": batch["action_id"],
        }
        result = client.post(url + "/execute-next", json=next_body)
        assert result.status_code == 200, result.text
    final = client.get(url)
    assert final.status_code == 200, final.text
    assert final.json()["state"] == "SERVICE_RECEIPTS_VERIFIED"
    assert final.json()["all_original_service_receipts_verified"]
    assert not final.json()["economic_experiment_verified"]
    final_rows = physical_snapshot(engine)
    assert client.post(url + "/execute-next", json=execute_body).json() == final.json()
    lookup = client.get(f"{base}/commands/{epoch_id}/by-key/{request['idempotency_key']}")
    assert lookup.status_code == 200 and lookup.json()["original"] == final.json()
    assert lookup.json()["command_kind"] == "PREPARE"
    assert lookup.json()["original_request"] == request
    assert physical_snapshot(engine) == final_rows
    with Session(engine) as session:
        operations = list(session.scalars(select(BankOperation)))
        assert len(operations) == len(portfolio["batches"])
        assert {op.idempotency_key for op in operations} == {
            b["bank_idempotency_key"] for b in portfolio["batches"]
        }
        assert (
            len(
                list(
                    session.scalars(
                        select(EvidenceItem).where(
                            EvidenceItem.source_type == "USER_FULL_ASSET_PORTFOLIO_CONFIRMATION"
                        )
                    )
                )
            )
            == 1
        )
        assert verify_audit_chain(session, DEMO_USER_ID, mode="EXACT").status == "VALID"
