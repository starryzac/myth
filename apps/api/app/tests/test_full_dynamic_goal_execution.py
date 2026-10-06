"""Direct synthetic capability risks; not actual bank or product acceptance evidence."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid5

import pytest
from app.db.models import ActionPlan, AuditEpoch, DecisionRun, EvidenceItem
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    BoundarySnapshot,
    CashFact,
    GoalMonthFact,
    GoalOwnership,
    SourceIssue,
)
from app.domain.execution import revalidate_execution
from app.domain.execution_types import BankCommand, ExecutionContext
from app.domain.full_dynamic_goal_execution import (
    ALGORITHM,
    MARKER,
    FullDynamicGoalInput,
    FullDynamicGoalPrepareRequest,
    build_full_dynamic_goal_effect,
    derive_full_dynamic_goal_proof,
    dynamic_goal_bank_key,
    validate_full_dynamic_goal_proof,
)
from app.domain.goal_allocation import IncomeLot
from app.domain.income_ledger import (
    IncomeFragment,
    IncomeLedger,
    IncomeOrigin,
    location_id,
    reserve_income,
)
from app.domain.multi_goal_allocation import SourceReference
from app.domain.policy_configuration import configuration_hash
from app.services import full_dynamic_goal_execution as service
from app.services.action_contracts import ActionResponse
from app.services.full_goals import FullGoalModelContent, ReviewedGoalHashes, canonical_goal_bridge
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)
USER, GOAL, POLICY, VERSION, CASH, DEST, EPOCH, MODEL, INCOME, BANK, ORIGIN, OWN, MONTH = (
    UUID(int=index) for index in range(1, 14)
)
HASH = "a" * 64


def fixture(**changes: Any) -> FullDynamicGoalInput:
    full, base = canonical_goal_bridge(
        {
            "type": "long_term_goal",
            "name": "original goal",
            "target_cents": 300000,
            "deadline": "2026-10-31",
            "monthly_contribution": {
                "min_cents": 50000,
                "target_cents": 100000,
                "max_cents": 150000,
            },
            "allow_partial": True,
        }
    )
    confirmed = NOW - timedelta(days=2)
    content = FullGoalModelContent(
        user_id=USER,
        epoch_id=EPOCH,
        goal_id=GOAL,
        policy_id=POLICY,
        base_policy_version_id=VERSION,
        expected_version_id=UUID(int=100),
        full_configuration=full,
        full_hash=configuration_hash(full),
        base_hash=configuration_hash(base),
        reviewed=ReviewedGoalHashes(
            full_hash=configuration_hash(full), base_hash=configuration_hash(base)
        ),
        accepted=True,
        confirmed_at=confirmed,
        idempotency_key="model-key",
        reason="explicit original model",
        request_hash=HASH,
    ).model_dump(mode="json")
    model_hash = configuration_hash(content)
    fragment = location_id(ORIGIN, CASH)
    ledger = IncomeLedger(
        user_id=USER,
        as_of=NOW,
        scope_account_ids=(CASH,),
        origins=(
            IncomeOrigin(
                origin_transaction_id=ORIGIN,
                origin_account_id=CASH,
                amount_cents=500000,
                occurred_at=NOW - timedelta(days=1),
                observed_at=NOW,
                bank_evidence_id=BANK,
                bank_evidence_hash=HASH,
            ),
        ),
        fragments=(
            IncomeFragment(
                fragment_id=fragment,
                origin_transaction_id=ORIGIN,
                account_id=CASH,
                available_cents=500000,
            ),
        ),
    )
    context = ExecutionContext(
        user_id=USER,
        snapshot=BoundarySnapshot(
            as_of=NOW,
            timezone="UTC",
            source_digest=HASH,
            cash_accounts=[
                CashFact(
                    account_id=CASH,
                    account_type="CASH",
                    balance_cents=700000,
                    observed_at=NOW,
                    evidence_ids=[BANK],
                ),
                CashFact(
                    account_id=DEST,
                    account_type="GOAL",
                    balance_cents=0,
                    observed_at=NOW,
                    evidence_ids=[OWN],
                ),
            ],
            goals=[
                GoalOwnership(
                    goal_id=GOAL,
                    policy_id=POLICY,
                    account_id=DEST,
                    cash_owned_cents=0,
                    principal_owned_cents=0,
                    allocated_cents=0,
                    evidence_ids=[OWN],
                )
            ],
            goal_month_contributions=[
                GoalMonthFact(
                    goal_id=GOAL,
                    period="2026-10",
                    contributed_cents=0,
                    evidence_ids=[MONTH],
                )
            ],
        ),
        versions=[
            BoundaryPolicyVersion(
                policy_id=POLICY,
                version_id=VERSION,
                configuration=base,
                content_hash=configuration_hash(base),
                confirmed_at=confirmed,
                valid_from=confirmed,
                evidence_ids=[MODEL],
            )
        ],
        positions=[],
        boundary_products=[],
        lots=[
            IncomeLot(
                origin_transaction_id=ORIGIN,
                account_id=CASH,
                fragment_id=fragment,
                amount_cents=500000,
                available_cents=500000,
                occurred_at=NOW - timedelta(days=1),
                observed_at=NOW,
                evidence_ids=[INCOME, BANK],
            )
        ],
    )
    values: dict[str, Any] = {
        "context": context,
        "request": FullDynamicGoalPrepareRequest(
            goal_id=GOAL,
            expected_policy_version_id=VERSION,
            expected_model_evidence_id=MODEL,
            expected_model_evidence_hash=model_hash,
            expected_epoch_id=EPOCH,
            idempotency_key="original-dynamic-key",
        ),
        "epoch_id": EPOCH,
        "model_original": content,
        "model_evidence_id": MODEL,
        "model_evidence_hash": model_hash,
        "income": ledger,
        "income_evidence_id": INCOME,
        "income_evidence_hash": HASH,
        "source_refs": [
            SourceReference(
                user_id=USER,
                evidence_id=identity,
                content_hash=model_hash if identity == MODEL else HASH,
            )
            for identity in (MODEL, INCOME, BANK, OWN, MONTH)
        ],
        "protection_policies": [],
        "protection_inventory_complete": True,
    }
    values.update(changes)
    return FullDynamicGoalInput.model_validate(values)


def alter_model(data: FullDynamicGoalInput, **changes: Any) -> FullDynamicGoalInput:
    raw = dict(data.model_original)
    full, base = canonical_goal_bridge({**raw["full_configuration"], **changes})
    raw.update(
        full_configuration=full,
        full_hash=configuration_hash(full),
        base_hash=configuration_hash(base),
        reviewed={"full_hash": configuration_hash(full), "base_hash": configuration_hash(base)},
    )
    hashed = configuration_hash(raw)
    version = data.context.versions[0].model_copy(
        update={
            "configuration": base,
            "content_hash": configuration_hash(base),
        }
    )
    return data.model_copy(
        update={
            "model_original": raw,
            "model_evidence_hash": hashed,
            "request": data.request.model_copy(update={"expected_model_evidence_hash": hashed}),
            "source_refs": [
                row.model_copy(update={"content_hash": hashed}) if row.evidence_id == MODEL else row
                for row in data.source_refs
            ],
            "context": data.context.model_copy(update={"versions": [version]}),
        }
    )


def test_above_nominal_is_within_actual_confirmed_max_and_old_path_remains_blocked() -> None:
    data = fixture()
    before = data.model_dump(mode="json")
    effect, proof = build_full_dynamic_goal_effect(data, UUID(int=200))
    assert effect.amount_cents == proof.dynamic_cap_cents == proof.remaining_max_cents == 150000
    assert proof.nominal_remaining_target_cents == 100000 and proof.minimum_cents == 50000
    assert proof.full_projection_input_hash and proof.status == "VERIFIED_RANGE"
    assert sum(use.amount_cents for use in effect.income_uses) == effect.amount_cents
    assert effect.fee_cents == effect.loss_cents == 0
    assert proof.bank_authority is False and validate_full_dynamic_goal_proof(
        effect, data.context, proof
    )
    original = revalidate_execution(effect, data.context)
    assert original.status == "BLOCKED" and "CURRENT_GOAL_PLAN_DISALLOWS_AMOUNT" in original.reasons
    assert data.model_dump(mode="json") == before
    assert "inputs" not in proof.model_dump(mode="json")


def test_fixed_amount_and_sources_do_not_increase_when_fresh_suggestion_increases() -> None:
    data = fixture()
    effect, _ = build_full_dynamic_goal_effect(data, UUID(int=200))
    smaller = effect.model_copy(
        update={
            "amount_cents": 100000,
            "cash_uses": [effect.cash_uses[0].model_copy(update={"amount_cents": 100000})],
            "income_uses": [effect.income_uses[0].model_copy(update={"amount_cents": 100000})],
        }
    )
    proof = derive_full_dynamic_goal_proof(data, smaller)
    assert proof.status == "VERIFIED_RANGE" and proof.dynamic_cap_cents == 150000
    assert smaller.amount_cents == 100000 and smaller.income_uses[0].amount_cents == 100000


def test_actual_month_repeated_contribution_reduces_max_and_rejects_original_oversize() -> None:
    data = fixture()
    effect, _ = build_full_dynamic_goal_effect(data, UUID(int=200))
    snapshot = data.context.snapshot.model_copy(
        update={
            "goals": [
                data.context.snapshot.goals[0].model_copy(
                    update={
                        "cash_owned_cents": 100000,
                        "allocated_cents": 100000,
                    }
                )
            ],
            "goal_month_contributions": [
                data.context.snapshot.goal_month_contributions[0].model_copy(
                    update={"contributed_cents": 100000}
                )
            ],
            "cash_accounts": [
                data.context.snapshot.cash_accounts[0],
                data.context.snapshot.cash_accounts[1].model_copy(update={"balance_cents": 100000}),
            ],
        }
    )
    current = data.model_copy(
        update={"context": data.context.model_copy(update={"snapshot": snapshot})}
    )
    reduced = derive_full_dynamic_goal_proof(current)
    assert reduced.dynamic_cap_cents == reduced.remaining_max_cents == 50000
    assert reduced.nominal_remaining_target_cents == 0
    assert derive_full_dynamic_goal_proof(current, effect).status == "BLOCKED"


def test_only_exact_own_reservation_restored_not_other_action_or_assigned_goal() -> None:
    data = fixture()
    effect, _ = build_full_dynamic_goal_effect(data, UUID(int=200))
    reserved = reserve_income(
        data.income, effect.operation_id, effect.income_uses, "ALLOCATE_GOAL", NOW
    )
    own = data.model_copy(update={"income": reserved, "own_effect": effect})
    assert derive_full_dynamic_goal_proof(own, effect).status == "VERIFIED_RANGE"
    other_reservation = reserved.reservations[0].model_copy(update={"action_id": UUID(int=201)})
    wrong = own.model_copy(
        update={"income": reserved.model_copy(update={"reservations": (other_reservation,)})}
    )
    assert derive_full_dynamic_goal_proof(wrong, effect).status == "UNKNOWN"
    assigned = data.income.fragments[0].model_copy(
        update={
            "assigned_cents": 450000,
            "available_cents": 50000,
        }
    )
    assigned_ledger = data.income.model_copy(update={"fragments": (assigned,)})
    reduced = data.model_copy(
        update={
            "income": assigned_ledger,
            "context": data.context.model_copy(
                update={
                    "lots": [data.context.lots[0].model_copy(update={"available_cents": 50000})]
                }
            ),
        }
    )
    assert derive_full_dynamic_goal_proof(reduced).dynamic_cap_cents == 50000


@pytest.mark.parametrize("field", ["reserved_cash_by_account", "source_issues", "versions"])
def test_claim_source_or_current_version_drift_cannot_reuse_proof(field: str) -> None:
    data = fixture()
    effect, proof = build_full_dynamic_goal_effect(data, UUID(int=200))
    changes: dict[str, Any] = {
        "reserved_cash_by_account": {CASH: 690000},
        "source_issues": [SourceIssue(code="UNKNOWN_OTHER_ACTION", entity_type="action")],
        "versions": [data.context.versions[0].model_copy(update={"version_id": UUID(int=1000)})],
    }
    context = data.context.model_copy(update={field: changes[field]})
    assert not validate_full_dynamic_goal_proof(effect, context, proof)
    assert derive_full_dynamic_goal_proof(
        data.model_copy(update={"context": context}), effect
    ).status
    assert derive_full_dynamic_goal_proof(
        data.model_copy(update={"context": context}), effect
    ).status != ("VERIFIED_RANGE")


def test_full_unknown_and_model_or_owner_tamper_preserve_unknown_not_fake_zero() -> None:
    for changes in (
        {"protection_issues": ["FULL_HISTORY_UNKNOWN"]},
        {"epoch_id": UUID(int=300)},
        {"model_evidence_hash": "b" * 64},
        {"source_refs": fixture().source_refs[:-1]},
    ):
        proof = derive_full_dynamic_goal_proof(fixture(**changes))
        assert proof.status == "UNKNOWN" and proof.dynamic_cap_cents is None
    owner = fixture()
    assert (
        derive_full_dynamic_goal_proof(
            owner.model_copy(
                update={
                    "source_refs": [
                        row.model_copy(update={"user_id": UUID(int=999)})
                        for row in owner.source_refs
                    ],
                }
            )
        ).status
        == "UNKNOWN"
    )


def test_deadline_and_old_window_veto_overdue_even_full_deferral_preview() -> None:
    data = alter_model(
        fixture(), deadline="2026-10-01", allow_deferral=True, deferral_cost_cents_per_day=7
    )
    result = derive_full_dynamic_goal_proof(data)
    assert result.status == "UNKNOWN" and "ORIGINAL_MVP_DEADLINE_PASSED" in result.reasons
    expired = fixture()
    version = expired.context.versions[0].model_copy(update={"valid_until": NOW})
    proof = derive_full_dynamic_goal_proof(
        expired.model_copy(
            update={
                "context": expired.context.model_copy(update={"versions": [version]}),
            }
        )
    )
    assert proof.status == "UNKNOWN" and "ORIGINAL_MVP_WINDOW_NOT_CURRENT" in proof.reasons


def test_partial_below_original_min_never_becomes_executable() -> None:
    data = fixture()
    fragment = data.income.fragments[0].model_copy(
        update={
            "assigned_cents": 480000,
            "available_cents": 20000,
        }
    )
    current = data.model_copy(
        update={
            "income": data.income.model_copy(update={"fragments": (fragment,)}),
            "context": data.context.model_copy(
                update={
                    "lots": [data.context.lots[0].model_copy(update={"available_cents": 20000})]
                }
            ),
        }
    )
    proof = derive_full_dynamic_goal_proof(current)
    assert proof.reserve and proof.reserve.status == "PARTIAL"
    assert proof.dynamic_cap_cents == 20000 and proof.minimum_cents == 50000
    assert proof.status == "BLOCKED"


def test_source_accounts_and_other_goals_cannot_lend_owned_or_reserved_income() -> None:
    data = fixture()
    second = data.context.snapshot.goals[0].model_copy(
        update={
            "goal_id": UUID(int=300),
            "policy_id": UUID(int=301),
            "account_id": CASH,
            "cash_owned_cents": 650000,
            "allocated_cents": 650000,
        }
    )
    snapshot = data.context.snapshot.model_copy(
        update={"goals": [*data.context.snapshot.goals, second]}
    )
    context = data.context.model_copy(update={"snapshot": snapshot})
    proof = derive_full_dynamic_goal_proof(data.model_copy(update={"context": context}))
    assert proof.dynamic_cap_cents is None or proof.dynamic_cap_cents <= 50000
    assert proof.status != "VERIFIED_RANGE" or proof.dynamic_cap_cents == 50000


@pytest.mark.parametrize(
    "field",
    [
        "amount_cents",
        "as_of",
        "user_id",
        "authority",
        "income_uses",
        "available_cents",
        "role",
        "future_income",
    ],
)
def test_http_identity_schema_rejects_financial_or_permission_overrides(field: str) -> None:
    raw = fixture().request.model_dump(mode="json")
    assert FullDynamicGoalPrepareRequest.model_validate_json(json.dumps(raw)) == fixture().request
    with pytest.raises(ValidationError):
        FullDynamicGoalPrepareRequest.model_validate_json(json.dumps({**raw, field: 1}))


def test_proof_numeric_tamper_and_effect_upsizing_do_not_pass_pure_consumer() -> None:
    data = fixture()
    effect, proof = build_full_dynamic_goal_effect(data, UUID(int=200))
    changed = proof.model_copy(update={"dynamic_cap_cents": 200000})
    assert not validate_full_dynamic_goal_proof(effect, data.context, changed)
    assert not validate_full_dynamic_goal_proof(
        effect.model_copy(update={"goal_id": UUID(int=999)}), data.context, proof
    )


def test_preconfirmation_and_principal_not_counted_as_new_available_income() -> None:
    data = fixture()
    origin = data.income.origins[0].model_copy(update={"occurred_at": NOW - timedelta(days=3)})
    lot = data.context.lots[0].model_copy(update={"occurred_at": origin.occurred_at})
    current = data.model_copy(
        update={
            "income": data.income.model_copy(update={"origins": (origin,)}),
            "context": data.context.model_copy(update={"lots": [lot]}),
        }
    )
    proof = derive_full_dynamic_goal_proof(current)
    assert proof.status == "BLOCKED" and proof.dynamic_cap_cents == 0
    assert proof.reserve and proof.reserve.future_income_included_cents == 0


def test_binding_recognition_detects_actual_prepare_when_marker_and_key_are_stripped() -> None:
    raw = {
        "decision_trace": {
            "algorithm_versions": {MARKER: ALGORITHM},
            "inputs": {"action_request": {MARKER: {"protocol": ALGORITHM}}},
        }
    }
    run = DecisionRun(
        id=UUID(int=900),
        user_id=USER,
        input_snapshot=raw,
        snapshot_hash=configuration_hash(raw),
        subject_action_plan_id=UUID(int=901),
    )
    action = ActionPlan(
        id=UUID(int=901),
        user_id=USER,
        decision_run_id=run.id,
        action_type="ALLOCATE_GOAL",
        idempotency_key="action:tampered",
        request={},
    )
    session = cast(Session, MagicMock(spec=Session))
    session.get.return_value = run  # type: ignore[attr-defined]
    assert service.has_full_dynamic_goal_binding(session, action)
    action.request = {MARKER: None}
    assert service.has_full_dynamic_goal_binding(session, action)
    action.request = {}
    action.idempotency_key = dynamic_goal_bank_key("original")
    assert service.has_full_dynamic_goal_binding(session, action)


def test_missing_original_goal_run_is_not_silent_legacy_fallback() -> None:
    fake = MagicMock(spec=Session)
    fake.get.return_value = None
    action = ActionPlan(
        id=UUID(int=901),
        user_id=USER,
        decision_run_id=UUID(int=900),
        action_type="ALLOCATE_GOAL",
        idempotency_key="action:tampered",
        request={},
    )
    with pytest.raises(PolicyLifecycleError, match="目标动作不能缺失"):
        service.has_full_dynamic_goal_binding(cast(Session, fake), action)


def test_missing_production_hook_is_explicitly_rejected_without_financial_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    def original(*args: Any) -> Any:
        calls.append(args)
        raise AssertionError("Default legacy planner must not be used")

    monkeypatch.setattr(service, "prepare_action", original)
    with pytest.raises(PolicyLifecycleError) as error:
        service.prepare_full_dynamic_goal_execution(
            cast(Engine, object()), USER, fixture().request, NOW
        )
    assert error.value.code == "DYNAMIC_GOAL_EXECUTION_NOT_IMPLEMENTED" and not calls


def test_original_key_not_found_has_exact_owner_filter_and_no_financial_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = MagicMock(spec=Session)
    fake.scalar.return_value = None
    monkeypatch.setattr(service, "_read_snapshot", lambda session: None)
    result = service.lookup_full_dynamic_goal_execution(
        cast(Session, fake), USER, "actual-key", NOW
    )
    assert result.status == "NOT_FOUND" and result.not_found_is_final is False
    assert (
        result.action is None and result.confirmation is None and result.current_authority is False
    )
    query = fake.scalar.call_args.args[0].compile().params
    assert set(query.values()) == {USER, dynamic_goal_bank_key("actual-key")}
    fake.get.assert_not_called()


@pytest.mark.parametrize("source", ["MISSING", "VERIFIED", "TAMPERED"])
def test_original_confirmation_lookup_never_infers_authority_from_authorized_status(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    data = fixture()
    effect, proof = build_full_dynamic_goal_effect(data, UUID(int=200))
    assert proof.effect_hash is not None
    confirmation_id = uuid5(effect.operation_id, "confirmation:" + proof.effect_hash)
    raw = {
        "execution": BankCommand(effect=effect, effect_hash=proof.effect_hash).model_dump(
            mode="json"
        ),
        MARKER: {"request": data.request.model_dump(mode="json")},
        "confirmation_evidence_id": str(confirmation_id),
    }
    action = ActionPlan(
        id=effect.operation_id,
        user_id=USER,
        status="AUTHORIZED",
        autonomy_level="ASK_ONCE",
        idempotency_key=dynamic_goal_bank_key(data.request.idempotency_key),
        request=raw,
        request_hash=configuration_hash(raw),
    )
    response = ActionResponse(
        user_id=USER,
        action_id=action.id,
        decision_run_id=UUID(int=999),
        status="AUTHORIZED",
        autonomy_level="ASK_ONCE",
        effect=effect,
        effect_hash=proof.effect_hash,
        prepared_at=NOW,
        as_of=NOW + timedelta(days=1),
        prepared_validation=revalidate_execution(effect, data.context),
    )
    content = {
        "simulation": True,
        "user_id": str(USER),
        "action_id": str(action.id),
        "effect_hash": proof.effect_hash,
        "accepted": True,
        "confirmed_at": NOW.isoformat(),
        "valid_until": effect.expires_at.isoformat(),
    }
    evidence = EvidenceItem(
        id=confirmation_id,
        user_id=USER,
        evidence_level="USER_CONFIRMED_ACTION",
        source_type="USER_ACTION_CONFIRMATION",
        source_ref=str(action.id),
        content=content,
        content_hash=configuration_hash(content),
        status="VALID",
        observed_at=NOW,
        valid_from=NOW,
        valid_to=effect.expires_at,
    )
    if source == "TAMPERED":
        evidence.content = {**content, "effect_hash": "b" * 64}
    fake = MagicMock(spec=Session)
    fake.scalar.return_value = action

    def get(model: Any, identity: UUID) -> Any:
        if model is ActionPlan and identity == action.id:
            return action
        if model is AuditEpoch and identity == data.epoch_id:
            return AuditEpoch(id=data.epoch_id, user_id=USER, status="SEALED")
        if model is EvidenceItem and identity == confirmation_id:
            return None if source == "MISSING" else evidence
        raise AssertionError("No unrelated financial source may be queried")

    fake.get.side_effect = get
    monkeypatch.setattr(service, "_read_snapshot", lambda session: None)
    monkeypatch.setattr(service, "read_original_dynamic_goal_request", lambda *args: data.request)
    monkeypatch.setattr(service, "get_action", lambda *args: response)
    if source == "TAMPERED":
        with pytest.raises(PolicyLifecycleError):
            service.lookup_full_dynamic_goal_execution(
                cast(Session, fake), USER, data.request.idempotency_key, NOW + timedelta(days=1)
            )
        return
    result = service.lookup_full_dynamic_goal_execution(
        cast(Session, fake), USER, data.request.idempotency_key, NOW + timedelta(days=1)
    )
    assert result.status == "RECORDED" and result.original_request == data.request
    assert (
        result.original_action_request == raw
        and result.server_request_hash == configuration_hash(raw)
    )
    assert result.client_request_hash == configuration_hash(data.request.model_dump(mode="json"))
    assert result.historical is True and result.epoch_state == "SEALED"
    assert result.current_authority is False and result.confirmation_is_current_authority is False
    if source == "MISSING":
        assert result.confirmation is None and result.confirmation_status == "MISSING"
    else:
        assert (
            result.confirmation is not None and result.confirmation.evidence_id == confirmation_id
        )
        assert result.confirmation.effect_hash == proof.effect_hash
        assert result.confirmation_status == "VERIFIED_AT_CONFIRMATION"
        assert result.confirmation_verified_at == NOW
        assert result.confirmation.expires_at < response.as_of
