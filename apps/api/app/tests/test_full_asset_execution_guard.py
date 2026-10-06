"""Direct integer combination risks; synthetic facts are not financial experiment evidence."""

from unittest.mock import Mock
from uuid import UUID

import pytest
from app.db.models import ActionPlan
from app.domain.boundary_types import BoundaryPosition
from app.domain.execution import revalidate_execution
from app.domain.full_asset_execution import FullAssetExecuteRequest, build_frozen_portfolio
from app.domain.full_asset_execution_guard import validate_remaining_combination
from app.services import full_asset_execution_dispatch as dispatch
from app.services.action_contracts import ActionReceiptResponse, ActionResponse
from app.services.full_asset_execution_store import (
    FullAssetBatchView,
    FullAssetExecutionResponse,
    batch_binding,
    select_original_batch,
)
from app.tests.test_full_asset_execution import literal_basis
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def test_exact_remaining_combination_keeps_actual_settled_principal_and_later_identity() -> None:
    request, basis = literal_basis()
    portfolio = build_frozen_portfolio(request, basis)
    first = portfolio.batches[0].command.effect
    assert first.position_id is not None
    current = basis.context.model_copy(
        update={
            "snapshot": basis.context.snapshot.model_copy(
                update={
                    "cash_accounts": [
                        a.model_copy(
                            update={
                                "balance_cents": a.balance_cents
                                - sum(
                                    u.amount_cents
                                    for u in first.cash_uses
                                    if u.account_id == a.account_id
                                )
                            }
                        )
                        for a in basis.context.snapshot.cash_accounts
                    ],
                }
            ),
            "positions": [
                *basis.context.positions,
                BoundaryPosition(
                    position_id=first.position_id,
                    goal_id=None,
                    principal_cents=first.amount_cents,
                    status="HELD",
                    principal_available_at=None,
                ),
            ],
            "exposure": basis.context.exposure.model_copy(
                update={
                    "managed_principal_cents": first.amount_cents,
                    "counted_position_ids": [
                        *basis.context.exposure.counted_position_ids,
                        first.position_id,
                    ],
                }
            )
            if basis.context.exposure
            else None,
        }
    )
    value = validate_remaining_combination(
        portfolio,
        current,
        [2],
        basis.as_of,
        current_full_configuration_hash=basis.full_policy_content_hash,
        full_sources=[],
        full_source_issues=[],
        full_inventory_complete=True,
    )
    assert value.remaining_batch_numbers == [2]
    assert value.remaining_purchase_cents == portfolio.batches[1].command.effect.amount_cents
    assert value.checked_point_count == 1098
    assert first.operation_id == portfolio.batches[0].action_id
    assert current.positions[-1].principal_available_at is None


@pytest.mark.parametrize(
    "risk", ["other-claim", "source-missing", "changed-full", "expired", "duplicate-batch"]
)
def test_pending_other_claims_sources_and_original_scope_cannot_be_subtracted(risk: str) -> None:
    request, basis = literal_basis()
    portfolio = build_frozen_portfolio(request, basis)
    context, now, numbers, full_hash, issues = (
        basis.context,
        basis.as_of,
        [1, 2],
        basis.full_policy_content_hash,
        [],
    )
    if risk == "other-claim":
        context = context.model_copy(update={"reserved_cash_by_account": {UUID(int=1): 400000}})
    if risk == "source-missing":
        issues = ["MISSING_ACTUAL_FULL_SOURCE"]
    if risk == "changed-full":
        full_hash = "b" * 64
    if risk == "expired":
        now = portfolio.expires_at
    if risk == "duplicate-batch":
        numbers = [1, 1]
    with pytest.raises(ValueError):
        validate_remaining_combination(
            portfolio,
            context,
            numbers,
            now,
            current_full_configuration_hash=full_hash,
            full_sources=[],
            full_source_issues=issues,
            full_inventory_complete=True,
        )


def test_explicit_bound_guard_call_without_marker_refuses_before_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, basis = literal_basis()
    command = build_frozen_portfolio(request, basis).batches[0].command
    action = ActionPlan(request={})
    engine = Mock(spec=Engine)
    monkeypatch.setattr(
        dispatch, "fresh_read", lambda _engine: pytest.fail("default performed a read")
    )
    with Session() as session:
        with pytest.raises(ValueError, match="marker缺失"):
            dispatch.enforce_full_asset_batch_phase1(engine, session, action, command, basis.as_of)
        with pytest.raises(ValueError, match="marker缺失"):
            dispatch.enforce_full_asset_batch_acceptance(
                engine, session, action, command, basis.as_of
            )
        assert not session.in_transaction()


def test_strict_marker_refuses_injected_effect_or_missing_identity_before_new_connection() -> None:
    request, basis = literal_basis()
    command = build_frozen_portfolio(request, basis).batches[0].command
    action = ActionPlan(
        request={"full_asset_execution": {"effect": command.model_dump(mode="json")}}
    )
    with Session() as session, pytest.raises(ValueError, match="marker"):
        dispatch.enforce_full_asset_batch_phase1(
            Mock(spec=Engine), session, action, command, basis.as_of
        )


def test_original_new_child_key_without_marker_cannot_fall_back_to_legacy() -> None:
    request, basis = literal_basis()
    batch = build_frozen_portfolio(request, basis).batches[0]
    action = ActionPlan(request={}, idempotency_key=batch.bank_idempotency_key)
    with Session() as session, pytest.raises(ValueError, match="marker缺失"):
        dispatch.enforce_full_asset_batch_phase1(
            Mock(spec=Engine), session, action, batch.command, basis.as_of
        )


def test_original_marker_json_uuids_reach_fresh_read_without_fake_financial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, basis = literal_basis()
    portfolio = build_frozen_portfolio(request, basis)
    batch = portfolio.batches[0]
    payload = {
        "full_asset_execution": batch_binding(portfolio, 1).model_dump(mode="json"),
        "execution": batch.command.model_dump(mode="json"),
    }
    from app.domain.policy_configuration import configuration_hash

    action = ActionPlan(
        id=batch.action_id,
        user_id=basis.user_id,
        request=payload,
        request_hash=configuration_hash(payload),
        idempotency_key=batch.bank_idempotency_key,
    )

    def reached(_engine: Engine) -> None:
        raise LookupError("ORIGINAL_MARKER_PARSED_FRESH_READ_REQUIRED")

    monkeypatch.setattr(dispatch, "fresh_read", reached)
    for guard in (
        dispatch.enforce_full_asset_batch_phase1,
        dispatch.enforce_full_asset_batch_acceptance,
    ):
        with Session() as session, pytest.raises(LookupError, match="FRESH_READ_REQUIRED"):
            guard(Mock(spec=Engine), session, action, batch.command, basis.as_of)


def test_deployment_without_both_real_original_pipeline_hooks_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import execution, execution_bank

    monkeypatch.delattr(execution, "FULL_ASSET_BATCH_GUARDS_VERSION", raising=False)
    monkeypatch.delattr(execution_bank, "FULL_ASSET_BATCH_GUARDS_VERSION", raising=False)
    with pytest.raises(ValueError, match="尚未接通"):
        dispatch.require_original_pipeline_guards()


def test_fixed_batch_replay_never_advances_and_bank_without_receipt_keeps_successor_blocked() -> (
    None
):
    """Synthetic typed selection inputs exercise identity/order, not bank proof."""
    request, basis = literal_basis()
    portfolio = build_frozen_portfolio(request, basis)
    views = []
    for batch in portfolio.batches:
        action = ActionResponse(
            user_id=basis.user_id,
            action_id=batch.action_id,
            decision_run_id=UUID(int=batch.batch_number),
            status="PLANNED",
            autonomy_level="ASK_ONCE",
            effect=batch.command.effect,
            effect_hash=batch.command.effect_hash,
            prepared_at=basis.as_of,
            as_of=basis.as_of,
            prepared_validation=revalidate_execution(batch.command.effect, basis.context),
        )
        views.append(
            FullAssetBatchView(
                batch_number=batch.batch_number,
                action_id=batch.action_id,
                bank_idempotency_key=batch.bank_idempotency_key,
                original_action=action,
                original_request_hash="a" * 64,
                original_trace_verified=True,
                current_action_missing=False,
            )
        )
    original = FullAssetExecutionResponse(
        user_id=basis.user_id,
        epoch_id=basis.epoch_id,
        as_of=basis.as_of,
        original_portfolio=portfolio,
        original_request_hash=portfolio.client_request_hash,
        state="PREPARED_UNRESERVED",
        original_consent_id=None,
        original_consent_evidence_id=None,
        original_consent=None,
        original_consent_verified=False,
        original_consent_evidence_status="NOT_RECORDED",
        current_epoch_open=True,
        batches=views,
        all_original_service_receipts_verified=False,
    )
    first_body = FullAssetExecuteRequest(
        accepted=True,
        reviewed_portfolio_hash=portfolio.portfolio_hash,
        expected_epoch_id=portfolio.epoch_id,
        expected_batch_number=1,
        expected_action_id=portfolio.batches[0].action_id,
    )
    second_body = first_body.model_copy(
        update={
            "expected_batch_number": 2,
            "expected_action_id": portfolio.batches[1].action_id,
        }
    )
    assert select_original_batch(original, first_body).batch_number == 1
    first = original.batches[0].original_action
    assert first is not None
    for state in ("PLANNED", "SUBMITTED", "UNKNOWN"):
        current = original.model_copy(
            update={
                "batches": [
                    original.batches[0].model_copy(
                        update={
                            "original_action": first.model_copy(
                                update={
                                    "status": state,
                                    "bank_status": "SETTLED" if state != "PLANNED" else None,
                                }
                            )
                        }
                    ),
                    original.batches[1],
                ]
            }
        )
        with pytest.raises(ValueError, match="先前原批次未决"):
            select_original_batch(current, second_body)
    receipt = ActionReceiptResponse(
        receipt_id=UUID(int=20),
        action_id=first.action_id,
        bank_operation_id=UUID(int=21),
        status="SUCCEEDED",
        executed_cents=first.effect.amount_cents,
        fee_cents=0,
        loss_cents=0,
        posting_ids=[UUID(int=22)],
        occurred_at=basis.as_of,
        reconciled_at=None,
    )
    first = first.model_copy(
        update={"status": "SUCCEEDED", "bank_status": "SETTLED", "receipt": receipt}
    )
    original = original.model_copy(
        update={
            "batches": [
                original.batches[0].model_copy(update={"original_action": first}),
                original.batches[1],
            ]
        }
    )
    assert select_original_batch(original, first_body).action_id == first.action_id
    selected = select_original_batch(original, first_body).original_action
    assert selected is not None and selected.receipt == receipt
    assert original.batches[1].original_action is not None
    assert original.batches[1].original_action.receipt is None
    assert select_original_batch(original, second_body).batch_number == 2
    with pytest.raises(ValueError, match="同一原批序"):
        select_original_batch(
            original, first_body.model_copy(update={"expected_action_id": UUID(int=99)})
        )
