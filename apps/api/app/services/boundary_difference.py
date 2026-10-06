"""Only original complete, audit-bound decision snapshots can enter difference replay."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.domain.boundary import compute_boundary
from app.domain.boundary_difference import (
    BoundaryDifference,
    VerifiedBoundaryInput,
    compare_verified_boundaries,
    unknown_difference,
)
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
)
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import IntentModel
from app.services.autonomy import _clock
from app.services.autonomy_envelope import _snapshot
from app.services.decision_trace import DecisionTraceResponse, get_decision_trace
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy.orm import Session

BoundaryField = Literal[
    "execution_boundary",
    "autonomy_boundary",
    "recovery_boundary",
    "asset_boundary",
    "goal_boundary",
    "maturity_boundary",
    "bank_projection_context",
]


class BoundaryDifferenceRequest(IntentModel):
    before_run_id: UUID
    after_run_id: UUID
    boundary_field: BoundaryField = "execution_boundary"


def _verified_input(record: DecisionTraceResponse, field: BoundaryField) -> VerifiedBoundaryInput:
    trace = record.trace
    if record.completeness != "COMPLETE" or record.audit_chain_status != "VALID" or trace is None:
        raise ValueError("ACTUAL_TRACE_OR_AUDIT_NOT_VERIFIED")
    verify_trace(trace)
    if (
        trace.user_id != record.user_id
        or trace.as_of != record.as_of
        or trace.run_id != record.run_id
    ):
        raise ValueError("ACTUAL_TRACE_IDENTITY_MISMATCH")
    raw = trace.inputs.get(field)
    input_path = "inputs." + field
    if raw is None:
        planning = trace.inputs.get("planning")
        if isinstance(planning, dict):
            raw = planning.get(field)
            input_path = "inputs.planning." + field
    if not isinstance(raw, dict) or set(raw) != {
        "snapshot",
        "versions",
        "positions",
        "products",
        "source_issues",
    }:
        raise ValueError("ORIGINAL_BOUNDARY_INPUT_MISSING")
    if raw["source_issues"]:
        raise ValueError("ORIGINAL_BOUNDARY_SOURCE_ISSUES")
    snapshot = BoundarySnapshot.model_validate_json(json.dumps(raw["snapshot"]))
    versions = tuple(
        BoundaryPolicyVersion.model_validate_json(json.dumps(row)) for row in raw["versions"]
    )
    positions = tuple(
        BoundaryPosition.model_validate_json(json.dumps(row)) for row in raw["positions"]
    )
    products = tuple(
        BoundaryProduct.model_validate_json(json.dumps(row)) for row in raw["products"]
    )
    if snapshot.as_of != trace.as_of or snapshot.source_issues:
        raise ValueError("ORIGINAL_BOUNDARY_CLOCK_OR_SOURCE_MISMATCH")
    needed: set[UUID] = set()
    for rows in (
        snapshot.cash_accounts,
        snapshot.bills,
        snapshot.occurrence_settlements,
        snapshot.goals,
        snapshot.goal_month_contributions,
        snapshot.unassigned_goal_cash,
        versions,
    ):
        for row in rows:
            if not row.evidence_ids:
                raise ValueError("ORIGINAL_FACT_SOURCE_BINDING_MISSING")
            needed.update(row.evidence_ids)
    for position in positions:
        needed.update(position.evidence_ids)
        needed.update(position.availability_evidence_ids)
        if not position.evidence_ids:
            raise ValueError("ORIGINAL_POSITION_SOURCE_BINDING_MISSING")
    source_map = {item.id: item for item in trace.sources}
    if len(source_map) != len(trace.sources) or not needed.issubset(source_map):
        raise ValueError("ORIGINAL_SOURCE_INVENTORY_MISSING")
    for identity in needed:
        item = source_map[identity]
        if (
            item.user_id != trace.user_id
            or item.status_at_decision != "VALID"
            or item.content_integrity != "VERIFIED"
            or configuration_hash(item.content) != item.content_hash
            or item.captured_content_hash != item.content_hash
            or not item.observed_at <= trace.as_of
            or not item.valid_from <= trace.as_of
            or (item.valid_to is not None and trace.as_of >= item.valid_to)
        ):
            raise ValueError("ORIGINAL_SOURCE_CONTENT_OR_WINDOW_INVALID")
    _policy_bindings(trace, versions, needed)
    _fact_bindings(trace, snapshot, positions, source_map)
    recorded = trace.outcome.get("validation")
    key = "baseline_boundary"
    if field in {"recovery_boundary", "maturity_boundary"}:
        recorded = trace.outcome.get("recovery")
        key = "actual_boundary"
    if not isinstance(recorded, dict) or not isinstance(recorded.get(key), dict):
        raise ValueError("ORIGINAL_RECORDED_BOUNDARY_RESULT_MISSING")
    original_result = BoundaryResult.model_validate_json(json.dumps(recorded[key]))
    recomputed = compute_boundary(snapshot, list(versions), list(positions), list(products))
    if recomputed != original_result:
        raise ValueError("ORIGINAL_BOUNDARY_RESULT_REPLAY_MISMATCH")
    return VerifiedBoundaryInput(
        run_id=trace.run_id,
        user_id=trace.user_id,
        trace_hash=trace.trace_hash,
        input_path=input_path,
        snapshot=snapshot,
        versions=versions,
        positions=positions,
        products=products,
        source_refs=tuple(sorted((str(key), source_map[key].content_hash) for key in needed)),
    )


def _fact_bindings(
    trace: DecisionTrace,
    snapshot: BoundarySnapshot,
    positions: tuple[BoundaryPosition, ...],
    sources: dict[UUID, TraceEvidence],
) -> None:
    def matches(ids: list[UUID], source_type: str, expected: dict[str, Any]) -> bool:
        expected = {"simulation": True, "user_id": str(trace.user_id), **expected}
        for identity in ids:
            item = sources[identity]
            if item.source_type != source_type or item.evidence_level != "BANK_CONFIRMED":
                continue
            if set(expected).issubset(item.content) and configuration_hash(
                {key: item.content[key] for key in expected}
            ) == configuration_hash(expected):
                return True
        return False

    for cash in snapshot.cash_accounts:
        if not matches(
            cash.evidence_ids,
            "SIMULATED_BANK_BALANCE",
            {
                "account_id": str(cash.account_id),
                "account_type": cash.account_type,
                "balance_cents": cash.balance_cents,
                "currency": "CNY",
                "as_of": cash.observed_at.isoformat(),
            },
        ):
            raise ValueError("ORIGINAL_CASH_FACT_SOURCE_MISMATCH")
    for bill in snapshot.bills:
        if not matches(
            bill.evidence_ids,
            "SIMULATED_CREDIT_CARD_BILL",
            {
                "bill_id": str(bill.bill_id),
                "account_id": str(bill.account_id),
                "statement_date": bill.statement_date.isoformat(),
                "due_date": bill.due_date.isoformat(),
                "total_cents": bill.total_cents,
                "paid_cents": bill.paid_cents,
                "status": bill.status,
            },
        ):
            raise ValueError("ORIGINAL_BILL_FACT_SOURCE_MISMATCH")
    for goal in snapshot.goals:
        if not matches(
            goal.evidence_ids,
            "SIMULATED_GOAL_OWNERSHIP",
            {
                "protocol": "goal-ownership-v1",
                "goal_id": str(goal.goal_id),
                "policy_id": str(goal.policy_id),
                "account_id": str(goal.account_id) if goal.account_id else None,
                "cash_owned_cents": goal.cash_owned_cents,
                "principal_owned_cents": goal.principal_owned_cents,
                "allocated_cents": goal.allocated_cents,
                "position_ids": sorted(
                    str(row.position_id)
                    for row in positions
                    if row.goal_id == goal.goal_id and row.status != "REDEEMED"
                ),
            },
        ):
            raise ValueError("ORIGINAL_GOAL_FACT_SOURCE_MISMATCH")
    for month in snapshot.goal_month_contributions:
        if not matches(
            month.evidence_ids,
            "SIMULATED_GOAL_MONTH_CONTRIBUTION",
            {
                "protocol": "goal-month-contribution-v1",
                "goal_id": str(month.goal_id),
                "period": month.period,
                "contributed_cents": month.contributed_cents,
                "complete": True,
            },
        ):
            raise ValueError("ORIGINAL_GOAL_MONTH_SOURCE_MISMATCH")
    for settlement in snapshot.occurrence_settlements:
        expected: dict[str, Any] = {
            "protocol": "recurring-settlement-v1",
            "policy_id": str(settlement.policy_id),
            "period": settlement.period,
            "paid_cents": settlement.paid_cents,
            "complete": True,
            "as_of": settlement.settled_at.isoformat(),
        }
        if settlement.final_total_cents is not None:
            expected["final_total_cents"] = settlement.final_total_cents
        if not matches(
            settlement.evidence_ids, "SIMULATED_RECURRING_SETTLEMENT", expected
        ) or not any(
            sources[key].content.get("final_total_cents") == settlement.final_total_cents
            for key in settlement.evidence_ids
        ):
            raise ValueError("ORIGINAL_SETTLEMENT_SOURCE_MISMATCH")
    for row in positions:
        if not matches(
            row.evidence_ids,
            "SIMULATED_BANK_POSITION",
            {
                "position_id": str(row.position_id),
                "goal_id": str(row.goal_id) if row.goal_id else None,
                "principal_cents": row.principal_cents,
                "status": row.status,
            },
        ):
            raise ValueError("ORIGINAL_POSITION_SOURCE_MISMATCH")
        if row.principal_available_at is not None and not matches(
            row.availability_evidence_ids,
            "SIMULATED_PRINCIPAL_AVAILABILITY",
            {
                "protocol": "principal-availability-v1",
                "position_id": str(row.position_id),
                "principal_cents": row.principal_cents,
                "goal_id": str(row.goal_id) if row.goal_id else None,
                "available_at": row.principal_available_at.isoformat(),
                "principal_return_bps": 10000,
                "rollover": False,
            },
        ):
            raise ValueError("ORIGINAL_PRINCIPAL_TIME_SOURCE_MISMATCH")
    for unassigned in snapshot.unassigned_goal_cash:
        goal_cash = next(
            (
                row
                for row in snapshot.cash_accounts
                if row.account_id == unassigned.account_id and row.account_type == "GOAL"
            ),
            None,
        )
        allocated = sum(
            row.cash_owned_cents
            for row in snapshot.goals
            if row.account_id == unassigned.account_id
        )
        if (
            goal_cash is None
            or unassigned.amount_cents != goal_cash.balance_cents - allocated
            or set(unassigned.evidence_ids) != set(goal_cash.evidence_ids)
        ):
            raise ValueError("ORIGINAL_UNASSIGNED_CASH_REPLAY_MISMATCH")


def _policy_bindings(
    trace: DecisionTrace, versions: tuple[BoundaryPolicyVersion, ...], needed: set[UUID]
) -> None:
    policies = {item.id: item for item in trace.policies}
    if len(policies) != len(trace.policies):
        raise ValueError("ORIGINAL_POLICY_INVENTORY_INVALID")
    for version in versions:
        original = policies.get(version.version_id)
        if (
            original is None
            or original.user_id != trace.user_id
            or original.policy_id != version.policy_id
            or original.configuration != version.configuration
            or original.configuration_integrity != "VERIFIED"
            or original.configuration_hash != version.content_hash
            or original.captured_configuration_hash != version.content_hash
            or configuration_hash(version.configuration) != version.content_hash
            or original.confirmed_at != version.confirmed_at
            or original.valid_from != version.valid_from
            or original.valid_to != version.valid_until
        ):
            raise ValueError("ORIGINAL_POLICY_VERSION_BINDING_INVALID")
        proofs = [
            item
            for item in trace.sources
            if item.id in needed
            and item.source_ref == str(version.version_id)
            and item.source_type == "POLICY_CONFIRMATION"
            and item.evidence_level == "USER_CONFIRMED_POLICY"
        ]
        if not any(
            item.content.get("user_id") == str(trace.user_id)
            and item.content.get("policy_id") == str(version.policy_id)
            and item.content.get("version_id") == str(version.version_id)
            and item.content.get("reviewed_hash") == version.content_hash
            and item.content.get("accepted") is True
            and _confirmation_time(item.content.get("confirmed_at"), version.confirmed_at)
            for item in proofs
        ):
            raise ValueError("ORIGINAL_POLICY_CONFIRMATION_MISSING")


def _confirmation_time(value: Any, expected: datetime) -> bool:
    if not isinstance(value, str):
        return False
    try:
        actual = datetime.fromisoformat(value)
        return actual.tzinfo is not None and actual.utcoffset() is not None and actual == expected
    except ValueError:
        return False


def compare_boundary_runs(
    session: Session, user_id: UUID, body: BoundaryDifferenceRequest, now: datetime
) -> BoundaryDifference:
    _snapshot(session)
    now = _clock(now)
    with session.no_autoflush:
        before = get_decision_trace(session, user_id, body.before_run_id, now)
        after = (
            before
            if body.before_run_id == body.after_run_id
            else get_decision_trace(session, user_id, body.after_run_id, now)
        )
        if before.as_of > after.as_of:
            raise PolicyLifecycleError("INVALID_DIFFERENCE_ORDER", "原决策时序不能倒置", 409)
        try:
            first = _verified_input(before, body.boundary_field)
            last = first if after is before else _verified_input(after, body.boundary_field)
        except (ValueError, TypeError, KeyError) as error:
            # Fixed errors and validation class only: never guess a financial fact.
            code = (
                str(error)
                if type(error) is ValueError and str(error).isupper()
                else "ORIGINAL_BOUNDARY_INPUT_INVALID"
            )
            return unknown_difference(body.before_run_id, body.after_run_id, [code])
        return compare_verified_boundaries(first, last)
