"""Synthetic pure planning/adapter risks, not actual bank or combination evidence."""

from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from app.domain.boundary_types import BillFact, CashFact
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import ExecutionValidation
from app.domain.full_recovery_execution import build_full_recovery_effect
from app.domain.full_recovery_next import (
    FullRecoveryNextRequest,
    derive_next_whole_selection,
    next_whole_v1_key,
)
from app.domain.full_recovery_planning import FullRecoveryPlanningResult, plan_full_recovery
from app.domain.policy_configuration import configuration_hash
from app.services import full_recovery_next as service
from app.services.action_contracts import ActionResponse
from app.services.full_recovery_execution import (
    FullRecoveryExecutionLookup,
    FullRecoveryExecutionPreview,
)
from app.services.full_recovery_planning import FullRecoveryPlanningResponse
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.product_catalog import CatalogReadResponse
from app.tests.test_full_recovery_execution import EPOCH, consent_fixture, fixture
from app.tests.test_full_recovery_planning import fixed_holding, holding, inputs
from app.tests.test_recovery import NOW, USER, reserve
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def request(key: str = "synthetic-original-root") -> FullRecoveryNextRequest:
    data = inputs()
    return FullRecoveryNextRequest(
        policy_id=data.policy_id,
        expected_version_id=data.policy_version_id,
        expected_epoch_id=EPOCH,
        idempotency_key=key,
    )


def planning(plan: FullRecoveryPlanningResult | None = None) -> FullRecoveryPlanningResponse:
    plan = plan or plan_full_recovery(inputs())
    return FullRecoveryPlanningResponse(
        user_id=USER,
        policy_id=plan.policy_id,
        as_of=NOW,
        state="COMPUTED",
        plan=plan,
        catalogue=CatalogReadResponse(
            state="REGISTERED",
            versions=[],
            unregistered_product_ids=[],
            issues=[],
            complete_within_registered_capacity=True,
        ),
        catalogue_bindings=[],
        source_evidence_ids=[],
        source_issues=[],
        input_hash=configuration_hash(plan.model_dump(mode="json")),
        limitations=["SYNTHETIC_PURE_ONLY_NOT_ACTUAL_CATALOGUE_OR_BANK"],
    )


def original_fixture(
    body: FullRecoveryNextRequest,
) -> tuple[FullRecoveryExecutionPreview, ActionResponse]:
    plan = plan_full_recovery(inputs())
    forwarded = derive_next_whole_selection(body, plan, USER, NOW).next_v1_request
    assert forwarded is not None
    data = fixture().model_copy(update={"request": forwarded, "deadline_at": plan.deadline_at})
    effect, proof = build_full_recovery_effect(data)
    delegated = FullRecoveryExecutionPreview(
        user_id=USER,
        original_request=forwarded,
        proof=proof,
        execution_effect=effect,
        limitations=["SYNTHETIC_SCOPE_NOT_FINANCIAL_PROOF"],
    )
    action = ActionResponse(
        user_id=USER,
        action_id=effect.operation_id,
        decision_run_id=UUID(int=990),
        status="PLANNED",
        autonomy_level="ASK_ONCE",
        effect=effect,
        effect_hash=execution_effect_hash(effect),
        prepared_at=NOW,
        as_of=NOW,
        prepared_validation=ExecutionValidation(
            status="CONFIRMATION_REQUIRED",
            effect_hash=execution_effect_hash(effect),
            baseline_boundary=plan.actual_boundary,
            reasons=["SYNTHETIC_NOT_BANK"],
        ),
    )
    return delegated, action


def recorded(body: FullRecoveryNextRequest) -> FullRecoveryExecutionLookup:
    preview, action = original_fixture(body)
    raw = {"SYNTHETIC_TEST_ONLY": preview.original_request.model_dump(mode="json")}
    return FullRecoveryExecutionLookup(
        user_id=USER,
        idempotency_key=next_whole_v1_key(body.idempotency_key),
        status="RECORDED",
        original_request=preview.original_request,
        original_action_request=raw,
        client_request_hash=configuration_hash(preview.original_request.model_dump(mode="json")),
        server_request_hash=configuration_hash(raw),
        action=action,
        epoch_state="OPEN",
    )


def fake_readers(
    monkeypatch: pytest.MonkeyPatch, original: FullRecoveryExecutionLookup | None = None
) -> None:
    monkeypatch.setattr(service, "_read_snapshot", lambda *_: None)
    monkeypatch.setattr(
        service,
        "lookup_full_recovery_execution",
        lambda _, user, key, at: (
            original
            or FullRecoveryExecutionLookup(
                user_id=user, idempotency_key=key, status="NOT_FOUND_NOT_FINAL"
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "read_full_policy",
        lambda *_: SimpleNamespace(
            policy_id=request().policy_id,
            template_name="RecoveryPolicy",
            epoch_id=EPOCH,
            current_version=SimpleNamespace(version_id=request().expected_version_id),
        ),
    )
    monkeypatch.setattr(service, "read_full_recovery_planning", lambda *_: planning())
    monkeypatch.setattr(
        service,
        "preview_full_recovery_execution",
        lambda _, user, body, at: original_fixture(request())[0].model_copy(
            update={"original_request": body}
        ),
    )


def test_original_multi_position_order_full_denominator_and_bytes_are_retained() -> None:
    original = plan_full_recovery(
        inputs([holding(11, 10000), holding(12, 10000), holding(13, 15000)])
    )
    before = original.model_dump_json()
    selected = derive_next_whole_selection(request(), original, USER, NOW)
    assert selected.current_candidate_denominator == selected.current_selected_denominator == 3
    assert [row.position_id for row in selected.eligibility] == [UUID(int=n) for n in (11, 12, 13)]
    assert selected.next_v1_request is not None and selected.next_v1_request.position_id == UUID(
        int=11
    )
    assert selected.next_v1_request.idempotency_key == next_whole_v1_key(request().idempotency_key)
    assert not selected.bank_authority and not selected.atomic_combination
    assert original.model_dump_json() == before


def test_late_t1_and_fixed_maturity_never_gain_extended_time_or_new_protocol() -> None:
    for data in (
        inputs([holding(delay=1)]),
        inputs([fixed_holding(early=False)]),
        inputs([fixed_holding()]),
    ):
        original = plan_full_recovery(data)
        result = derive_next_whole_selection(request(), original, USER, NOW)
        assert result.next_v1_request is None and len(result.eligibility) == len(
            original.candidates
        )
        assert result.current_candidate_denominator == 1
        assert original.deadline_at == NOW
        assert result.eligibility[0].reasons


def test_future_bill_due_date_cannot_replace_original_current_protection_deadline() -> None:
    data = inputs([holding(delay=1)])
    card_id = UUID(int=985)
    actual_snapshot = data.snapshot.model_copy(
        update={
            "cash_accounts": [
                *data.snapshot.cash_accounts,
                CashFact(
                    account_id=card_id,
                    account_type="CREDIT_CARD",
                    balance_cents=0,
                    observed_at=NOW,
                    evidence_ids=[UUID(int=6)],
                ),
            ],
            "bills": [
                BillFact(
                    bill_id=UUID(int=986),
                    account_id=card_id,
                    statement_date=(NOW - timedelta(days=1)).date(),
                    due_date=(NOW + timedelta(days=2)).date(),
                    total_cents=50000,
                    paid_cents=0,
                    status="UNPAID",
                    evidence_ids=[UUID(int=6)],
                )
            ],
        }
    )
    original = plan_full_recovery(
        data.model_copy(
            update={
                "snapshot": actual_snapshot,
                "boundary_versions": [reserve(40000)],
            }
        )
    )
    selected = derive_next_whole_selection(request(), original, USER, NOW)
    assert original.deadline_at == NOW and original.candidates[0].on_time is False
    assert selected.next_v1_request is None and original.status == "LIQUIDITY_RISK"
    assert original.uncovered_checkpoints
    assert "NO_DEADLINE_EXTENSION" in " ".join(selected.eligibility[0].reasons)
    assert original.actual_scope_cash_cents == 70000


@pytest.mark.parametrize("kind", ["duplicate", "missing", "changed"])
def test_missing_duplicate_or_substituted_selected_candidate_refuses(kind: str) -> None:
    plan = plan_full_recovery(inputs())
    changes: dict[str, Any]
    if kind == "duplicate":
        changes = {"candidates": plan.candidates * 2}
    elif kind == "missing":
        changes = {"candidates": []}
    else:
        changes = {"lossless_steps": [plan.lossless_steps[0].model_copy(update={"net_cents": 1})]}
    with pytest.raises(ValueError, match="denominators"):
        derive_next_whole_selection(request(), plan.model_copy(update=changes), USER, NOW)


@pytest.mark.parametrize(
    "field", ["amount_cents", "position_id", "now", "deadline_at", "role", "quote", "receipt"]
)
def test_public_request_cannot_supply_money_position_time_bank_result(field: str) -> None:
    with pytest.raises(ValidationError):
        FullRecoveryNextRequest.model_validate_json(
            __import__("json").dumps({**request().model_dump(mode="json"), field: 1})
        )


def test_stable_key_does_not_depend_on_selected_position_or_current_version() -> None:
    assert next_whole_v1_key("a/b") == next_whole_v1_key("a/b")
    assert next_whole_v1_key("a/b") != next_whole_v1_key("a/B")
    assert len(next_whole_v1_key("x" * 120)) <= 120
    for value in ("", " ", "x" * 121):
        with pytest.raises(ValueError):
            next_whole_v1_key(value)


def test_ready_preview_delegates_actual_v1_but_does_not_call_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_readers(monkeypatch)
    monkeypatch.setattr(
        service, "prepare_full_recovery_execution", lambda *_: pytest.fail("readonly wrote")
    )
    result = service.preview_next_whole_recovery(cast(Session, object()), USER, request(), NOW)
    assert result.status == "READY_TO_PREPARE" and result.planning is not None
    assert result.selection is not None and result.selection.current_candidate_denominator == 1
    assert result.original_v1_preview is not None and not result.grants_authority
    assert (
        len(result.planning.plan.actual_boundary.calculation_trace) == 1098
        if result.planning.plan
        else False
    )


@pytest.mark.parametrize("action_status", ["PLANNED", "UNKNOWN"])
def test_recorded_root_key_never_reads_new_plan_or_calls_prepare(
    monkeypatch: pytest.MonkeyPatch,
    action_status: str,
) -> None:
    body = request()
    original = recorded(body)
    assert original.action is not None
    original = original.model_copy(
        update={
            "action": original.action.model_copy(
                update={
                    "status": action_status,
                    "bank_status": "SETTLED" if action_status == "UNKNOWN" else None,
                }
            )
        }
    )
    fake_readers(monkeypatch, original)
    monkeypatch.setattr(service, "audit_command_guard", lambda *_: nullcontext())
    monkeypatch.setattr(service, "_reader", lambda *_: nullcontext(cast(Session, object())))
    monkeypatch.setattr(
        service, "read_full_policy", lambda *_: pytest.fail("replayed fresh policy")
    )
    monkeypatch.setattr(
        service, "prepare_full_recovery_execution", lambda *_: pytest.fail("advanced position")
    )
    principal = consent_fixture()[0].principal_at_confirmation
    result = service.prepare_next_whole_recovery(cast(Engine, object()), USER, body, principal, NOW)
    assert result == original.action
    lookup = service.lookup_next_whole_recovery(
        cast(Session, object()), USER, body.idempotency_key, NOW
    )
    assert lookup.bound_request == body and lookup.original_v1_lookup == original
    assert not lookup.original_v2_request_separately_recorded and not lookup.automatically_advances
    with pytest.raises(PolicyLifecycleError, match="root-key"):
        service.preview_next_whole_recovery(
            cast(Session, object()),
            USER,
            body.model_copy(update={"expected_version_id": UUID(int=999)}),
            NOW,
        )


def test_new_prepare_calls_only_original_v1_with_same_full_body_and_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_readers(monkeypatch)
    monkeypatch.setattr(service, "audit_command_guard", lambda *_: nullcontext())
    monkeypatch.setattr(service, "_reader", lambda *_: nullcontext(cast(Session, object())))
    calls: list[tuple[Any, ...]] = []
    action = original_fixture(request())[1]

    def writer(*args: Any) -> ActionResponse:
        calls.append(args)
        return action

    monkeypatch.setattr(service, "prepare_full_recovery_execution", writer)
    engine = cast(Engine, object())
    principal = consent_fixture()[0].principal_at_confirmation
    assert service.prepare_next_whole_recovery(engine, USER, request(), principal, NOW) == action
    assert calls == [
        (engine, USER, original_fixture(request())[0].original_request, principal, NOW)
    ]


@pytest.mark.parametrize("fault", ["owner", "key", "hash", "missing"])
def test_original_lookup_drift_cannot_release_or_advance_root_request(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    original = recorded(request())
    changes: dict[str, Any] = (
        {"user_id": UUID(int=999)}
        if fault == "owner"
        else (
            {"idempotency_key": "changed"}
            if fault == "key"
            else (
                {"client_request_hash": "b" * 64} if fault == "hash" else {"original_request": None}
            )
        )
    )
    fake_readers(monkeypatch, original.model_copy(update=changes))
    with pytest.raises(PolicyLifecycleError):
        service.lookup_next_whole_recovery(
            cast(Session, object()), USER, request().idempotency_key, NOW
        )


def test_not_found_remains_nonfinal_unknown_source_does_not_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_readers(monkeypatch)
    unknown = planning().model_copy(update={"state": "UNKNOWN", "plan": None})
    monkeypatch.setattr(service, "read_full_recovery_planning", lambda *_: unknown)
    result = service.preview_next_whole_recovery(cast(Session, object()), USER, request(), NOW)
    assert result.status == "UNKNOWN" and result.selection is None and result.planning == unknown
    assert (
        result.existing.status == "NOT_FOUND_NOT_FINAL" and not result.existing.not_found_is_final
    )


def test_non_user_cannot_prepare_even_original_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_readers(monkeypatch, recorded(request()))
    principal = consent_fixture()[0].principal_at_confirmation.model_copy(update={"role": "AGENT"})
    with pytest.raises(PolicyLifecycleError, match="USER"):
        service.prepare_next_whole_recovery(cast(Engine, object()), USER, request(), principal, NOW)
