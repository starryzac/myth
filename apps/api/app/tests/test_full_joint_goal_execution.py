"""Synthetic direct risks, never runtime financial or actor-study evidence."""

import copy
import json
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models import ActionPlan
from app.domain.full_action_set_boundary_actual import actual_producer_keys
from app.domain.full_action_set_joint_producers import JointActionSetInput, joint_goal_ids
from app.domain.full_dynamic_goal_execution import _originals
from app.domain.full_joint_goal_execution import (
    FullJointGoalConfirmRequest,
    FullJointGoalExecuteRequest,
    FullJointGoalExecutionInput,
    FullJointGoalPrepareRequest,
    _scope,
    apply_joint_batch_cap,
    derive_joint_execution_plan,
    require_fixed_child,
    verify_frozen_joint_plan,
)
from app.domain.full_joint_goal_planning import bind_full_joint_input
from app.domain.full_policy_configuration import GoalAllocationPolicy
from app.domain.full_protection_projection import project_full_protection
from app.domain.multi_goal_allocation import (
    HardProtectionPoint,
    SourceReference,
    solve_multi_goal_allocation,
)
from app.domain.policy_configuration import configuration_hash
from app.services.full_goals import FullGoalModelContent, _request_hash
from app.services.full_joint_goal_execution_dispatch import require_original_joint_pipeline_hooks
from app.services.full_joint_goal_execution_guards import (
    has_full_joint_goal_binding,
    read_original_joint_child_request,
)
from app.services.full_policy_lifecycle import FullLifecycleResult, FullPolicyView, FullVersionView
from app.tests.test_full_action_set_boundary_actual import coverage
from app.tests.test_full_action_set_joint_producers import fixture as original_fixture
from app.tests.test_full_dynamic_goal_execution import EPOCH, NOW, USER
from pydantic import ValidationError
from sqlalchemy.orm import Session

POLICY = UUID(int=90001)
VERSION = UUID(int=90002)
CONFIRMATION = UUID(int=90003)
COMMAND = UUID(int=90004)


def _replace(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_replace(row, mapping) for row in value]
    if isinstance(value, dict):
        return {key: _replace(row, mapping) for key, row in value.items()}
    return value


def fixture(*, cap: int = 150000) -> FullJointGoalExecutionInput:
    """Two complete original goals share actual synthetic income, no new funds."""
    joint = original_fixture()
    actual = joint.original_actual_input
    first = actual.dynamic_goals[0]
    assert (
        first.data is not None
        and joint.original_joint_input is not None
        and joint.actual_planning is not None
        and joint.protection_inputs is not None
    )
    mapping = {
        str(UUID(int=value)): str(UUID(int=value + 1000)) for value in (2, 3, 4, 6, 8, 12, 13, 140)
    }
    second = type(first).model_validate_json(
        json.dumps(_replace(first.model_dump(mode="json"), mapping))
    )
    assert second.data is not None
    second = second.model_copy(update={"candidate_key": "goal:" + str(second.data.request.goal_id)})
    assert second.data is not None
    model = FullGoalModelContent.model_validate_json(json.dumps(second.data.model_original))
    model = model.model_copy(
        update={
            "request_hash": _request_hash(
                model.user_id,
                model.epoch_id,
                model.goal_id,
                model.expected_version_id,
                model.full_configuration,
                model.reason,
                model.idempotency_key,
            )
        }
    )
    raw = copy.deepcopy(actual.base.original_inventory)
    for table in ("goals", "accounts", "policies", "policy_versions"):
        for row in list(raw[table]):
            if row["id"] in mapping:
                raw[table].append(_replace(row, mapping))
    for row in list(raw["evidence_items"]):
        if row["id"] in mapping:
            cloned = _replace(row, mapping)
            if row["id"] == str(first.data.model_evidence_id):
                cloned["content"] = model.model_dump(mode="json")
            cloned["content_hash"] = configuration_hash(cloned["content"])
            raw["evidence_items"].append(cloned)
    original_proofs = {row["id"]: row for row in raw["evidence_items"]}
    context = first.data.context
    second_context = second.data.context
    shared = context.model_copy(
        update={
            "versions": [*context.versions, *second_context.versions],
            "snapshot": context.snapshot.model_copy(
                update={
                    "cash_accounts": [
                        *context.snapshot.cash_accounts,
                        second_context.snapshot.cash_accounts[1],
                    ],
                    "goals": [*context.snapshot.goals, *second_context.snapshot.goals],
                    "goal_month_contributions": [
                        *context.snapshot.goal_month_contributions,
                        *second_context.snapshot.goal_month_contributions,
                    ],
                }
            ),
        }
    )
    refs = [
        SourceReference(user_id=USER, evidence_id=UUID(row["id"]), content_hash=row["content_hash"])
        for row in raw["evidence_items"]
    ]
    values = []
    for producer in (first, second):
        assert producer.data is not None
        data = producer.data
        content = original_proofs[str(data.model_evidence_id)]
        data = data.model_copy(
            update={
                "context": shared,
                "source_refs": refs,
                "model_original": content["content"],
                "model_evidence_hash": content["content_hash"],
                "request": data.request.model_copy(
                    update={"expected_model_evidence_hash": content["content_hash"]}
                ),
            }
        )
        values.append(producer.model_copy(update={"data": data}))
    basis = copy.deepcopy(actual.base.financial_basis)
    basis.update(
        snapshot=shared.snapshot.model_dump(mode="json"),
        versions=[row.model_dump(mode="json") for row in shared.versions],
        evidence=[{"id": row["id"], "hash": row["content_hash"]} for row in raw["evidence_items"]],
    )
    actual = actual.model_copy(
        update={
            "base": actual.base.model_copy(
                update={
                    "original_inventory": raw,
                    "financial_basis": basis,
                    "financial_input_hash": configuration_hash(basis),
                    "expected_candidate_keys": actual_producer_keys(raw),
                }
            ),
            "table_coverage": coverage(raw),
            "dynamic_goals": values,
        }
    )
    protection = joint.protection_inputs.model_copy(
        update={"snapshot": shared.snapshot, "boundary_versions": shared.versions}
    )
    projection = project_full_protection(protection)
    points = [
        HardProtectionPoint(
            date=row.date,
            cash_cents=row.cash_cents,
            obligation_floor_cents=row.protected_cents_by_reason.get("obligations", 0),
            living_floor_cents=row.protected_cents_by_reason.get("living", 0),
            emergency_floor_cents=row.protected_cents_by_reason.get("emergency", 0),
            owned_goal_cash_cents=row.protected_cents_by_reason.get("goal_cash", 0),
            other_protection_floor_cents=row.protected_cents_by_reason.get("goal_minimum", 0),
            source_refs=refs,
        )
        for row in projection.original_annual_projection.calculation_trace
    ]
    originals = [_originals(row.data)[0] for row in values if row.data is not None]
    original = joint.original_joint_input.model_copy(
        update={
            "goals": originals,
            "hard_protection_points": points,
            "income_lots": [
                row.model_copy(update={"source_refs": refs})
                for row in joint.original_joint_input.income_lots
            ],
        }
    )
    binding = bind_full_joint_input(original, projection, refs)
    old = joint.actual_planning.original_joint.model_copy(
        update={
            "registered_goal_count": 2,
            "included_goal_ids": joint_goal_ids(actual),
            "allocation": solve_multi_goal_allocation(original),
            "input_hash": configuration_hash(original.model_dump(mode="json")),
            "source_evidence_ids": [row.evidence_id for row in refs],
        }
    )
    full = joint.actual_planning.full_protection.model_copy(
        update={
            "projection": projection,
            "source_evidence_ids": [row.evidence_id for row in refs],
            "input_digest": configuration_hash(
                {
                    "original_financial_digest": protection.snapshot.source_digest,
                    "full_input_hash": projection.input_hash,
                }
            ),
        }
    )
    allocation = solve_multi_goal_allocation(binding.candidate)
    planning = joint.actual_planning.model_copy(
        update={
            "original_joint": old,
            "full_protection": full,
            "binding": binding,
            "allocation": allocation,
            "reasons": sorted(set([*binding.reasons, *allocation.reasons])),
        }
    )
    joint = joint.model_copy(
        update={
            "original_actual_input": actual,
            "expected_goal_ids": joint_goal_ids(actual),
            "original_joint_input": original,
            "actual_planning": planning,
            "protection_inputs": protection,
            "verified_source_refs": refs,
        }
    )
    return add_scope(joint, cap)


def add_scope(joint: JointActionSetInput, cap: int) -> FullJointGoalExecutionInput:
    raw = copy.deepcopy(joint.original_actual_input.base.original_inventory)
    config = GoalAllocationPolicy.model_validate_json(
        json.dumps(
            {
                "type": "goal_allocation",
                "name": "synthetic joint scope",
                "goal_ids": [row["id"] for row in raw["goals"]],
                "max_single_allocation_cents": cap,
            }
        )
    ).model_dump(mode="json")
    confirmed = NOW - timedelta(minutes=5)
    request = {
        "kind": "CREATE",
        "user_id": str(USER),
        "epoch_id": str(EPOCH),
        "body": {
            "accepted": True,
            "reviewed_hash": configuration_hash(config),
            "idempotency_key": "scope-create",
            "reason": "SYNTHETIC_ONLY",
            "template_name": "GoalAllocationPolicy",
            "configuration": config,
        },
    }
    digest = configuration_hash(request)
    confirmation = {
        "protocol": "full-policy-confirmation-v1",
        "user_id": str(USER),
        "epoch_id": str(EPOCH),
        "policy_id": str(POLICY),
        "version_id": str(VERSION),
        "template_name": "GoalAllocationPolicy",
        "reviewed_hash": configuration_hash(config),
        "confirmed_at": confirmed.isoformat(),
        "accepted": True,
        "bank_authority": False,
        "confirmation_evidence_id": str(CONFIRMATION),
        "request_key": "scope-create",
        "request_hash": digest,
    }
    refs = [
        {
            "role": "goal",
            "kind": "GOAL",
            "id": row["id"],
            "binding_hash": configuration_hash(
                {"id": row["id"], "owner": str(USER), "policy_version_id": row["policy_version_id"]}
            ),
            "snapshot": row,
        }
        for row in raw["goals"]
    ]
    version = FullVersionView(
        version_id=VERSION,
        policy_id=POLICY,
        version_number=1,
        configuration=config,
        content_hash=configuration_hash(config),
        previous_hash=None,
        summary="synthetic",
        confirmation=confirmation,
        confirmed_at=confirmed,
        valid_from=confirmed,
        valid_until=None,
        change_reason="SYNTHETIC_ONLY",
        evidence_ids=[CONFIRMATION],
        impact_analysis={"reference_snapshots": refs},
        confirmation_evidence_status="CURRENT_EVIDENCE_MATCHED",
    )
    view = FullPolicyView(
        policy_id=POLICY,
        epoch_id=EPOCH,
        template_name="GoalAllocationPolicy",
        name="synthetic",
        status="ACTIVE",
        effective_status="ACTIVE",
        planning_confirmation_valid=True,
        reference_validation="CURRENT",
        current_version=version,
        updated_at=confirmed,
    )
    raw["full_policies"].append(
        {
            "id": str(POLICY),
            "user_id": str(USER),
            "epoch_id": str(EPOCH),
            "created_at": confirmed.isoformat(),
            "template_name": "GoalAllocationPolicy",
            "status": "ACTIVE",
        }
    )
    raw["full_policy_versions"].append(
        {
            "id": str(VERSION),
            "user_id": str(USER),
            "policy_id": str(POLICY),
            "created_at": confirmed.isoformat(),
            "version_number": 1,
            "configuration": config,
            "content_hash": version.content_hash,
            "previous_hash": None,
            "confirmed_at": confirmed.isoformat(),
            "valid_from": confirmed.isoformat(),
            "valid_until": None,
            "confirmation": confirmation,
            "evidence_ids": [str(CONFIRMATION)],
            "impact_analysis": {"reference_snapshots": refs},
        }
    )
    result = FullLifecycleResult(
        policy_id=POLICY,
        epoch_id=EPOCH,
        version_id=VERSION,
        command_id=COMMAND,
        status="ACTIVE",
        configuration_hash=version.content_hash,
    ).model_dump(mode="json")
    raw["full_policy_commands"].append(
        {
            "id": str(COMMAND),
            "user_id": str(USER),
            "policy_id": str(POLICY),
            "epoch_id": str(EPOCH),
            "version_id": str(VERSION),
            "created_at": confirmed.isoformat(),
            "kind": "CREATE",
            "command_number": 1,
            "previous_hash": None,
            "previous_status": None,
            "resulting_status": "ACTIVE",
            "idempotency_key": "scope-create",
            "request": request,
            "request_hash": digest,
            "result": result,
            "result_hash": configuration_hash(result),
        }
    )
    raw["evidence_items"].append(
        {
            "id": str(CONFIRMATION),
            "user_id": str(USER),
            "created_at": confirmed.isoformat(),
            "source_type": "FULL_POLICY_CONFIRMATION",
            "source_ref": str(VERSION),
            "evidence_level": "USER_CONFIRMED_POLICY",
            "content": confirmation,
            "content_hash": configuration_hash(confirmation),
            "status": "VALID",
            "observed_at": confirmed.isoformat(),
            "valid_from": confirmed.isoformat(),
            "valid_to": None,
        }
    )
    actual = joint.original_actual_input
    actual = actual.model_copy(
        update={
            "base": actual.base.model_copy(update={"original_inventory": raw}),
            "table_coverage": coverage(raw),
        }
    )
    body = FullJointGoalPrepareRequest(
        full_policy_id=POLICY,
        expected_full_policy_version_id=VERSION,
        expected_epoch_id=EPOCH,
        idempotency_key="joint-original-key",
    )
    return FullJointGoalExecutionInput(
        request=body, scope=view, joint=joint.model_copy(update={"original_actual_input": actual})
    )


def test_synthetic_whole_cap_differs_from_single_goal_and_preserves_originals() -> None:
    data = fixture()
    original = data.model_dump_json()
    result = derive_joint_execution_plan(data)
    assert result.status == "READY_TO_REVIEW", result.reasons
    assert result.plan is not None
    plan = result.plan
    assert plan.total_allocation_cents == 150000
    assert [child.command.effect.amount_cents for child in plan.children] == [50000, 100000]
    assert len(plan.allocation_input.hard_protection_points) == 1098
    assert len(plan.children) == 2 and plan.funds_reserved is False and plan.bank_authority is False
    assert data.joint.original_joint_input is not None
    assert plan.allocation_input.income_lots == data.joint.original_joint_input.income_lots
    assert data.model_dump_json() == original
    verify_frozen_joint_plan(plan)


@pytest.mark.parametrize("cap", [True, 0, -1, 2**63, 1.5])
def test_invalid_cap_cannot_be_a_numeric_permission(cap: Any) -> None:
    data = original_fixture()
    assert data.original_joint_input is not None
    with pytest.raises(ValueError):
        apply_joint_batch_cap(data.original_joint_input, cap)


def test_all_hard_floors_and_source_denominators_remain_with_lower_cap() -> None:
    data = original_fixture()
    assert data.original_joint_input is not None
    original = data.original_joint_input
    bounded = apply_joint_batch_cap(original, 100)
    assert bounded.goals == original.goals and bounded.income_lots == original.income_lots
    for before, after in zip(
        original.hard_protection_points, bounded.hard_protection_points, strict=True
    ):
        assert after.remaining_cents == min(before.remaining_cents, 100)
        assert after.obligation_floor_cents == before.obligation_floor_cents
        assert after.owned_goal_cash_cents == before.owned_goal_cash_cents


@pytest.mark.parametrize("field", ["amount_cents", "facts", "now", "role", "accepted", "result"])
def test_public_json_requests_do_not_accept_financial_or_permission_fields(field: str) -> None:
    values = {
        "full_policy_id": str(POLICY),
        "expected_full_policy_version_id": str(VERSION),
        "expected_epoch_id": str(EPOCH),
        "idempotency_key": "original-key",
    }
    assert (
        FullJointGoalPrepareRequest.model_validate_json(json.dumps(values)).full_policy_id == POLICY
    )
    with pytest.raises(ValidationError):
        FullJointGoalPrepareRequest.model_validate_json(json.dumps(values | {field: 100}))


@pytest.mark.parametrize("accepted", [False, 1, "true", None])
def test_whole_confirmation_requires_actual_strict_true(accepted: Any) -> None:
    with pytest.raises(ValidationError):
        FullJointGoalConfirmRequest.model_validate_json(
            json.dumps(
                {
                    "accepted": accepted,
                    "reviewed_plan_hash": "a" * 64,
                    "expected_epoch_id": str(EPOCH),
                    "idempotency_key": "consent-key",
                }
            )
        )


@pytest.mark.parametrize(
    "case",
    [
        "missing_confirmation",
        "unknown_scope",
        "wrong_epoch",
        "hash",
        "command",
        "reference",
        "version",
        "suspended",
        "future",
    ],
)
def test_scope_current_source_and_continuous_history_gate(case: str) -> None:
    data = fixture()
    encoded = data.model_dump(mode="json")
    raw = encoded["joint"]["original_actual_input"]["base"]["original_inventory"]
    if case == "missing_confirmation":
        raw["evidence_items"] = [
            row for row in raw["evidence_items"] if row["id"] != str(CONFIRMATION)
        ]
    elif case == "unknown_scope":
        encoded["scope"]["reference_validation"] = "CHANGED_OR_UNAVAILABLE"
    elif case == "wrong_epoch":
        encoded["request"]["expected_epoch_id"] = str(UUID(int=99999))
    elif case == "hash":
        raw["full_policy_versions"][-1]["content_hash"] = "a" * 64
    elif case == "command":
        raw["full_policy_commands"][-1]["previous_hash"] = "a" * 64
    elif case == "reference":
        raw["full_policy_versions"][-1]["impact_analysis"]["reference_snapshots"][0][
            "binding_hash"
        ] = "a" * 64
    elif case == "version":
        encoded["request"]["expected_full_policy_version_id"] = str(UUID(int=99998))
    elif case == "suspended":
        raw["full_policies"][-1]["status"] = "SUSPENDED"
    else:
        raw["full_policy_versions"][-1]["confirmed_at"] = (NOW + timedelta(minutes=1)).isoformat()
    altered = FullJointGoalExecutionInput.model_validate_json(json.dumps(encoded))
    with pytest.raises((ValueError, StopIteration, KeyError)):
        _scope(altered)
    preview = derive_joint_execution_plan(altered)
    assert (
        preview.plan is None
        and preview.status == "UNKNOWN"
        and len(preview.registered_goal_ids) == 2
    )


def test_missing_shared_hook_refuses_without_any_financial_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import execution_bank

    monkeypatch.setattr(execution_bank, "FULL_JOINT_GOAL_GUARDS_VERSION", None, raising=False)
    with pytest.raises(Exception) as caught:
        require_original_joint_pipeline_hooks()
    assert getattr(caught.value, "code", None) == "JOINT_ORIGINAL_PIPELINE_NOT_IMPLEMENTED"


def test_fixed_execute_response_loss_replay_never_advances_next_child() -> None:
    preview = derive_joint_execution_plan(fixture())
    assert preview.plan is not None, preview.reasons
    plan = preview.plan
    body = FullJointGoalExecuteRequest(
        accepted=True,
        reviewed_plan_hash=plan.plan_hash,
        expected_epoch_id=EPOCH,
        expected_child_number=1,
        expected_action_id=plan.children[0].action_id,
    )
    assert (
        require_fixed_child(plan, body, ["ORIGINAL_RECEIPT_VERIFIED", "AUTHORIZED"]).child_number
        == 1
    )
    next_body = body.model_copy(
        update={"expected_child_number": 2, "expected_action_id": plan.children[1].action_id}
    )
    for state in ("UNKNOWN", "SUBMITTED", "MISSING", "SETTLED", "SUCCEEDED"):
        with pytest.raises(ValueError, match="PRIOR_CHILD"):
            require_fixed_child(plan, next_body, [state, "AUTHORIZED"])
    with pytest.raises(ValueError):
        require_fixed_child(
            plan,
            body.model_copy(update={"expected_action_id": plan.children[1].action_id}),
            ["AUTHORIZED", "AUTHORIZED"],
        )


def test_solver_partial_below_original_minimum_does_not_become_executable() -> None:
    preview = derive_joint_execution_plan(fixture(cap=99999))
    assert preview.status in {"BLOCKED", "UNKNOWN"} and preview.plan is None
    assert len(preview.registered_goal_ids) == 2


def test_persisted_child_index_blocks_simultaneous_marker_and_key_stripping() -> None:
    class IndexOnlySession:
        def __init__(self) -> None:
            self.calls = 0

        def scalar(self, query: object) -> object:
            self.calls += 1
            return "full_joint_goal_execution_children" if self.calls == 1 else UUID(int=77)

    # No monetary evaluation is mocked: only the durable association lookup.
    fake = IndexOnlySession()
    action = ActionPlan(
        id=UUID(int=99),
        user_id=USER,
        idempotency_key="stripped-legacy-key",
        request={"execution": {}},
        request_hash=configuration_hash({"execution": {}}),
    )
    assert has_full_joint_goal_binding(cast(Session, fake), action) is True and fake.calls == 2
    with pytest.raises(Exception) as caught:
        read_original_joint_child_request(action)
    assert getattr(caught.value, "code", None) == "JOINT_PERSISTED_CHILD_MARKER_REQUIRED"


def test_self_hashed_parent_cannot_change_fixed_source_uses_or_whole_cap() -> None:
    preview = derive_joint_execution_plan(fixture())
    assert preview.plan is not None, preview.reasons
    from app.domain.full_joint_goal_execution import FullJointFrozenPlan

    encoded = preview.plan.model_dump(mode="json")
    encoded["children"][0]["command"]["effect"]["income_uses"][0]["amount_cents"] += 1
    changed = encoded["children"][0]["command"]
    # A valid self-hashed economic shape must still match the frozen solver.
    changed["effect"]["amount_cents"] += 1
    changed["effect"]["cash_uses"][0]["amount_cents"] += 1
    from app.domain.execution import execution_effect_hash
    from app.domain.execution_types import ExecutionEffect

    changed["effect_hash"] = execution_effect_hash(
        ExecutionEffect.model_validate_json(json.dumps(changed["effect"]))
    )
    encoded["plan_hash"] = configuration_hash(
        {key: value for key, value in encoded.items() if key != "plan_hash"}
    )
    with pytest.raises(ValidationError):
        FullJointFrozenPlan.model_validate_json(json.dumps(encoded))
