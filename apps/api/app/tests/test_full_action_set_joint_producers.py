"""Synthetic direct source/math/identity risks only, never actual finance evidence."""

import copy
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_action_set_joint_producers import (
    ALGORITHM,
    JointActionSetInput,
    JointOriginalCommand,
    derive_joint_producers,
    joint_action_ids,
    joint_goal_ids,
    match_joint_effect,
)
from app.domain.full_dynamic_goal_execution import _originals, build_full_dynamic_goal_effect
from app.domain.full_joint_goal_planning import bind_full_joint_input
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.multi_goal_allocation import (
    AllocationIncomeLot,
    HardProtectionPoint,
    MultiGoalAllocationInput,
    solve_multi_goal_allocation,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_action_set_joint_producers as service
from app.services.dashboard_types import DashboardAuditCard
from app.services.full_goals import FullGoalModelContent, _request_hash
from app.services.full_joint_goal_planning import FullJointPlanningResponse
from app.services.full_projection import FutureIncomeProjection, _checkpoint
from app.services.full_protection_projection import FullAnnualProtectionResponse
from app.services.multi_goal_planning import JointPlanningResponse
from app.tests.test_full_action_set_boundary_actual import coverage, trace_for
from app.tests.test_full_action_set_boundary_actual import fixture as actual_fixture
from app.tests.test_full_dynamic_goal_execution import EPOCH, NOW, USER, alter_model
from pydantic import ValidationError


def fixture(*, matching_original_effect: bool = True) -> JointActionSetInput:
    actual = actual_fixture()
    dynamic = actual.dynamic_goals[0]
    assert dynamic.data is not None
    original = dynamic.data
    if matching_original_effect:
        original = alter_model(
            original,
            monthly_contribution={
                "min_cents": 50000,
                "target_cents": 100000,
                "max_cents": 100000,
            },
        )
    model = FullGoalModelContent.model_validate_json(json.dumps(original.model_original))
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
    content = model.model_dump(mode="json")
    digest = configuration_hash(content)
    data = original.model_copy(
        update={
            "model_original": content,
            "model_evidence_hash": digest,
            "request": original.request.model_copy(update={"expected_model_evidence_hash": digest}),
            "source_refs": [
                row.model_copy(update={"content_hash": digest})
                if row.evidence_id == original.model_evidence_id
                else row
                for row in original.source_refs
            ],
        }
    )
    raw = copy.deepcopy(actual.base.original_inventory)
    version = data.context.versions[0]
    confirmation = dict(raw["policy_versions"][0]["confirmation"])
    confirmation["reviewed_hash"] = version.content_hash
    raw["policy_versions"][0].update(
        configuration=version.configuration,
        content_hash=version.content_hash,
        confirmation=confirmation,
    )
    confirmation_proof = next(
        row for row in raw["evidence_items"] if row["source_type"] == "POLICY_CONFIRMATION"
    )
    confirmation_proof.update(content=confirmation, content_hash=configuration_hash(confirmation))
    data = data.model_copy(
        update={
            "source_refs": [
                row.model_copy(update={"content_hash": confirmation_proof["content_hash"]})
                if str(row.evidence_id) == confirmation_proof["id"]
                else row
                for row in data.source_refs
            ]
        }
    )
    proof = next(row for row in raw["evidence_items"] if row["id"] == str(data.model_evidence_id))
    proof.update(
        content=content,
        content_hash=digest,
        observed_at=model.confirmed_at.isoformat(),
        valid_from=model.confirmed_at.isoformat(),
        created_at=model.confirmed_at.isoformat(),
    )
    goal, _ = _originals(data)
    raw["goals"][0].update(
        policy_version_id=str(goal.effective_policy_version_id),
        account_id=str(goal.account_id),
        target_cents=goal.target_cents,
        monthly_min_cents=goal.monthly_min_cents,
        monthly_target_cents=goal.monthly_target_cents,
        monthly_max_cents=goal.monthly_max_cents,
        minimum_protection_cents=goal.minimum_guarantee_cents,
        importance=goal.importance,
        deadline=goal.deadline.isoformat(),
    )
    raw_basis = dict(actual.base.financial_basis)
    if "evidence" in raw_basis:
        by_id = {row["id"]: row for row in raw["evidence_items"]}
        raw_basis["evidence"] = [
            {"id": row["id"], "hash": by_id[row["id"]]["content_hash"]}
            for row in raw_basis["evidence"]
        ]
    raw_basis.update(
        versions=[row.model_dump(mode="json") for row in data.context.versions],
        positions=[],
        products=[],
    )
    actual = actual.model_copy(
        update={
            "base": actual.base.model_copy(
                update={
                    "original_inventory": raw,
                    "financial_basis": raw_basis,
                    "financial_input_hash": configuration_hash(raw_basis),
                }
            ),
            "table_coverage": coverage(raw),
            "dynamic_goals": [dynamic.model_copy(update={"data": data})],
        }
    )
    protection = FullProtectionProjectionInput(
        snapshot=data.context.snapshot,
        boundary_versions=data.context.versions,
        positions=[],
        boundary_products=[],
        policies=[],
    )
    projection = project_full_protection(protection)
    refs = data.source_refs
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
    origins = {row.origin_transaction_id: row for row in data.income.origins}
    lots = [
        AllocationIncomeLot(
            fragment_id=row.fragment_id,
            origin_transaction_id=row.origin_transaction_id,
            account_id=row.account_id,
            received_cents=origins[row.origin_transaction_id].amount_cents,
            available_cents=row.available_cents,
            occurred_at=origins[row.origin_transaction_id].occurred_at,
            observed_at=origins[row.origin_transaction_id].observed_at,
            bank_evidence_id=origins[row.origin_transaction_id].bank_evidence_id,
            bank_evidence_hash=origins[row.origin_transaction_id].bank_evidence_hash,
            source_refs=refs,
        )
        for row in data.income.fragments
        if row.available_cents > 0
    ]
    inputs = MultiGoalAllocationInput(
        user_id=USER,
        as_of=NOW,
        timezone=data.context.snapshot.timezone,
        income_lots=lots,
        hard_protection_points=points,
        goals=[goal],
    )
    binding = bind_full_joint_input(inputs, projection, refs)
    allocation = solve_multi_goal_allocation(binding.candidate)
    curve = projection.full_annual_projection
    assert curve is not None
    checkpoint = [
        _checkpoint(
            index,
            curve.calculation_trace[index * 3].date,
            curve.calculation_trace[index * 3 : index * 3 + 3],
        )
        for index in range(366)
    ]
    full_digest = configuration_hash(
        {
            "original_financial_digest": protection.snapshot.source_digest,
            "full_input_hash": projection.input_hash,
        }
    )
    full = FullAnnualProtectionResponse(
        user_id=USER,
        as_of=NOW,
        projection=projection,
        initial_checkpoint=checkpoint[0],
        daily_checkpoints=checkpoint[1:],
        full_policy_sources=[],
        future_income=FutureIncomeProjection(),
        source_evidence_ids=[ref.evidence_id for ref in refs],
        source_issues=[],
        input_digest=full_digest,
        audit=DashboardAuditCard(
            epoch_id=EPOCH, status="VALID", complete=True, anchored_run_statuses={}
        ),
        limitations=["SYNTHETIC_ONLY"],
    )
    old = JointPlanningResponse(
        user_id=USER,
        as_of=NOW,
        registered_goal_count=1,
        independent_bank_projection_matched=True,
        included_goal_ids=[goal.goal_id],
        uncovered_goal_ids=[],
        allocation=solve_multi_goal_allocation(inputs),
        conflict=None,
        state="COMPUTED",
        source_evidence_ids=[ref.evidence_id for ref in refs],
        source_issues=[],
        input_hash=configuration_hash(inputs.model_dump(mode="json")),
        limitations=["SYNTHETIC_ONLY"],
    )
    planning = FullJointPlanningResponse(
        user_id=USER,
        as_of=NOW,
        original_joint=old,
        full_protection=full,
        binding=binding,
        allocation=allocation,
        conflict=None,
        state="COMPUTED",
        reasons=sorted(set(allocation.reasons)),
        input_hash=configuration_hash(
            {
                "original_joint_hash": old.input_hash,
                "full_protection_hash": full_digest,
                "binding_hash": binding.binding_hash,
                "reasons": sorted(set(allocation.reasons)),
            }
        ),
        limitations=["SYNTHETIC_ONLY"],
    )
    return JointActionSetInput(
        original_actual_input=actual,
        expected_goal_ids=joint_goal_ids(actual),
        original_action_ids=[],
        original_commands=[],
        original_joint_input=inputs,
        actual_planning=planning,
        protection_inputs=protection,
        verified_source_refs=refs,
    )


def test_complete_single_goal_joint_maps_only_to_the_exact_existing_effect() -> None:
    data = fixture()
    before = data.model_dump_json()
    result = derive_joint_producers(data)
    assert result.joint_family_complete, result.reasons
    assert result.results[0].view.state == "INCLUDED"
    assert result.results[0].view.amount_cents == 100000
    assert result.results[0].shadow_original_candidate_key == "goal:" + str(
        data.expected_goal_ids[0]
    )
    assert result.different_allocation_execution == "NOT_IMPLEMENTED_FOR_DIFFERENT_ALLOCATION"
    assert result.bank_authority is False and result.financial_write is False
    assert data.model_dump_json() == before


def test_original_nominal_joint_amount_does_not_shadow_larger_dynamic_effect() -> None:
    data = fixture(matching_original_effect=False)
    result = derive_joint_producers(data)
    assert not result.joint_family_complete, result.model_dump(mode="json")
    assert "NOT_IMPLEMENTED_FOR_DIFFERENT_ALLOCATION_OR_SOURCE_USES" in result.reasons
    assert result.results[0].view.state == "UNKNOWN"
    assert result.results[0].shadow_original_candidate_key is None


@pytest.mark.parametrize(
    "field",
    [
        "goals",
        "owner",
        "clock",
        "source",
        "income",
        "model",
        "rawgoal",
        "denominator",
        "projection",
        "permission",
        "solver",
        "audit",
        "policy",
    ],
)
def test_missing_or_changed_real_source_never_becomes_a_smaller_successful_joint(
    field: str,
) -> None:
    data = fixture()
    original, planning, protection = (
        data.original_joint_input,
        data.actual_planning,
        data.protection_inputs,
    )
    assert original is not None and planning is not None and protection is not None
    update: dict[str, Any] = {}
    if field == "goals":
        update["expected_goal_ids"] = []
    elif field == "owner":
        update["original_joint_input"] = original.model_copy(update={"user_id": UUID(int=9001)})
    elif field == "clock":
        update["original_joint_input"] = original.model_copy(
            update={"as_of": NOW + timedelta(seconds=1)}
        )
    elif field == "source":
        update["verified_source_refs"] = []
    elif field == "income":
        update["original_joint_input"] = original.model_copy(update={"income_lots": []})
    elif field in {"model", "permission"}:
        actual = data.original_actual_input
        row = actual.dynamic_goals[0]
        update["original_actual_input"] = actual.model_copy(
            update={
                "dynamic_goals": [
                    row.model_copy(
                        update={"data": None} if field == "model" else {"authority": None}
                    )
                ]
            }
        )
    elif field in {"rawgoal", "policy"}:
        actual = data.original_actual_input
        raw = copy.deepcopy(actual.base.original_inventory)
        if field == "rawgoal":
            raw["goals"][0]["target_cents"] += 1
        else:
            raw["full_policies"].append(
                {
                    "id": str(UUID(int=1001)),
                    "user_id": str(USER),
                    "epoch_id": str(EPOCH),
                    "status": "ACTIVE",
                    "template_name": "GoalAllocationPolicy",
                }
            )
        update["original_actual_input"] = actual.model_copy(
            update={
                "base": actual.base.model_copy(update={"original_inventory": raw}),
                "table_coverage": coverage(raw),
            }
        )
    elif field == "denominator":
        update["protection_inputs"] = protection.model_copy(
            update={"snapshot": protection.snapshot.model_copy(update={"horizon_days": 364})}
        )
    elif field == "projection":
        update["protection_inputs"] = protection.model_copy(
            update={
                "snapshot": protection.snapshot.model_copy(
                    update={
                        "cash_accounts": [
                            row.model_copy(update={"balance_cents": row.balance_cents + 1})
                            for row in protection.snapshot.cash_accounts
                        ]
                    }
                )
            }
        )
    elif field == "solver":
        update["original_joint_input"] = original.model_copy(update={"solver_node_budget": 1})
    else:
        update["actual_planning"] = planning.model_copy(
            update={
                "full_protection": planning.full_protection.model_copy(
                    update={
                        "audit": planning.full_protection.audit.model_copy(
                            update={"complete": False}
                        )
                    }
                )
            }
        )
    data = data.model_copy(update=update)
    if field == "owner":
        with pytest.raises(ValidationError, match="same user"):
            derive_joint_producers(data)
        return
    result = derive_joint_producers(data)
    assert not result.joint_family_complete and result.reasons
    assert len(result.results) == len(joint_goal_ids(data.original_actual_input))
    assert all(
        row.view.state == "UNKNOWN"
        and row.view.amount_cents is None
        and row.shadow_original_candidate_key is None
        for row in result.results
    )


@pytest.mark.parametrize("change", ["amount", "source", "goal", "destination", "version"])
def test_a_joint_allocation_cannot_relabel_a_different_original_effect(change: str) -> None:
    data = fixture()
    item = data.original_actual_input.dynamic_goals[0]
    assert (
        item.data is not None
        and data.original_joint_input is not None
        and data.actual_planning is not None
    )
    effect, _ = build_full_dynamic_goal_effect(item.data, UUID(int=9002))
    allocation = data.actual_planning.allocation
    assert allocation is not None
    goal = data.original_joint_input.goals[0]
    values: dict[str, Any] = {
        "amount": allocation.goals[0].amount_cents,
        "uses": allocation.income_uses,
        "goal_id": goal.goal_id,
        "destination": goal.account_id,
        "version": goal.effective_policy_version_id,
    }
    if change == "amount":
        assert type(values["amount"]) is int
        values["amount"] += 1
    elif change == "source":
        values["uses"] = [
            row.model_copy(update={"source_account_id": UUID(int=9003)})
            for row in allocation.income_uses
        ]
    else:
        values["goal_id" if change == "goal" else change] = UUID(int=9004)
    with pytest.raises(ValueError, match="NOT_IMPLEMENTED_FOR_DIFFERENT"):
        match_joint_effect(effect, **values)


@pytest.mark.parametrize("status", ["PLANNED", "AUTHORIZED", "SUBMITTED", "UNKNOWN", "SUCCEEDED"])
def test_missing_original_command_cannot_hide_historical_responsibility(status: str) -> None:
    data = fixture()
    actual = data.original_actual_input
    raw = copy.deepcopy(actual.base.original_inventory)
    identity = UUID(int=9900)
    raw["action_plans"].append(
        {
            "id": str(identity),
            "user_id": str(USER),
            "action_type": "ALLOCATE_GOAL",
            "status": status,
            "request": {},
        }
    )
    actual = actual.model_copy(
        update={
            "base": actual.base.model_copy(update={"original_inventory": raw}),
            "table_coverage": coverage(raw),
        }
    )
    data = data.model_copy(
        update={
            "original_actual_input": actual,
            "original_action_ids": joint_action_ids(actual),
            "original_commands": [JointOriginalCommand(action_id=identity)],
        }
    )
    result = derive_joint_producers(data)
    assert result.unresolved_original_action_ids == [identity]
    assert not result.joint_family_complete
    assert result.results[0].view.state == "UNKNOWN"


@pytest.mark.parametrize(
    "field", ["amount_cents", "bank_authority", "clock", "result", "current_permissions"]
)
def test_internal_typed_input_rejects_extra_financial_or_success_claims(field: str) -> None:
    data = fixture()
    with pytest.raises(ValidationError):
        JointActionSetInput.model_validate(data.model_dump() | {field: 1})


def test_old_trace_algorithm_cannot_be_relabelled_joint() -> None:
    with pytest.raises(ValueError, match="exact joint"):
        service.verify_frozen_joint_producers(trace_for(actual_fixture()))
    assert ALGORITHM == "full-policy-joint-action-producers-v1"


def joint_trace(data: JointActionSetInput) -> DecisionTrace:
    old = trace_for(data.original_actual_input)
    result = derive_joint_producers(data)
    raw = {
        UUID(row["id"]): row
        for row in data.original_actual_input.base.original_inventory["evidence_items"]
    }
    sources = [
        source.model_copy(
            update={
                "observed_at": NOW.fromisoformat(raw[source.id]["observed_at"]),
                "valid_from": NOW.fromisoformat(raw[source.id]["valid_from"]),
                "valid_to": None
                if raw[source.id].get("valid_to") is None
                else NOW.fromisoformat(raw[source.id]["valid_to"]),
            }
        )
        for source in old.sources
    ]
    return build_trace(
        run_id=old.run_id,
        user_id=USER,
        as_of=NOW,
        phase="EVALUATION",
        action_id=None,
        algorithm_versions={"joint_action_producers": ALGORITHM},
        inputs={"joint_action_set_input": data.model_dump(mode="json")},
        sources=sources,
        policies=old.policies,
        constraints=[],
        candidates=[],
        outcome={"joint_action_set_result": result.model_dump(mode="json")},
    )


def test_original_trace_replays_math_and_full_original_source_copies() -> None:
    data = fixture()
    trace = joint_trace(data)
    result = service.verify_frozen_joint_producers(trace)
    assert result == derive_joint_producers(data) and result.joint_family_complete


@pytest.mark.parametrize("change", ["hash", "duplicate"])
def test_self_hashed_financial_basis_cannot_rewrite_original_evidence_metadata(change: str) -> None:
    data = fixture()
    actual = data.original_actual_input
    basis = copy.deepcopy(actual.base.financial_basis)
    if change == "hash":
        basis["evidence"][0]["hash"] = "a" * 64
    else:
        basis["evidence"].append(basis["evidence"][0])
    actual = actual.model_copy(
        update={
            "base": actual.base.model_copy(
                update={
                    "financial_basis": basis,
                    "financial_input_hash": configuration_hash(basis),
                }
            )
        }
    )
    result = derive_joint_producers(data.model_copy(update={"original_actual_input": actual}))
    assert result.status == "UNKNOWN" and not result.joint_family_complete
    assert "JOINT_ORIGINAL_FINANCIAL_SOURCE_METADATA_NOT_BOUND" in result.reasons
    assert result.results[0].view.state == "UNKNOWN"


@pytest.mark.parametrize("change", ["missing", "clock", "identity"])
def test_trace_hash_alone_cannot_replace_original_source_coverage(change: str) -> None:
    trace = joint_trace(fixture())
    sources = list(trace.sources)
    if change == "missing":
        sources = []
    elif change == "clock":
        sources[0] = sources[0].model_copy(update={"observed_at": NOW + timedelta(seconds=1)})
    else:
        sources[0] = sources[0].model_copy(update={"source_ref": "different-original"})
    forged = build_trace(
        run_id=trace.run_id,
        user_id=USER,
        as_of=NOW,
        phase="EVALUATION",
        action_id=None,
        algorithm_versions=trace.algorithm_versions,
        inputs=trace.inputs,
        sources=sources,
        policies=trace.policies,
        constraints=[],
        candidates=[],
        outcome=trace.outcome,
    )
    with pytest.raises(ValueError):
        service.verify_frozen_joint_producers(forged)
