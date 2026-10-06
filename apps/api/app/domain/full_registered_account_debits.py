"""Conservative per-account bounds from one already verified FULL projection.

This module does not obtain facts, evaluate permissions, run the engine again or
credit future principal. Its adapter must supply the actual same-invocation
original execution context/validation and FULL input/result, never HTTP facts.
"""

import json
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import ExecutionContext, ExecutionEffect, ExecutionValidation
from app.domain.full_policy_configuration import validate_full_configuration
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    FullProtectionProjectionResult,
)
from app.domain.policy_configuration import MoneyCents, configuration_hash
from pydantic import Field, StrictInt, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SignedCents = Annotated[StrictInt, Field(ge=-(2**63), le=2**63 - 1)]


class FullAccountDailyDebitBound(BoundaryModel):
    day: Annotated[StrictInt, Field(ge=0, le=365)]
    date: date
    registered_outflow_cents: MoneyCents
    cumulative_registered_outflows_cents: MoneyCents
    account_cash_lower_bound_cents: SignedCents


class FullAccountDebitBound(BoundaryModel):
    account_id: UUID
    status: Literal["PASSED", "BLOCKED", "UNKNOWN"]
    actual_projected_cash_cents: MoneyCents | None
    goal_owned_cash_cents: MoneyCents | None
    other_claims_cents: MoneyCents | None
    initial_free_cash_cents: SignedCents | None
    maximum_cumulative_registered_outflows_cents: MoneyCents | None
    minimum_account_cash_lower_bound_cents: SignedCents | None
    daily_bounds: Annotated[list[FullAccountDailyDebitBound], Field(max_length=366)]
    reasons: list[str]


class FullAccountDebitBoundsProof(BoundaryModel):
    protocol: Literal["full-registered-account-debit-bounds-v1"] = (
        "full-registered-account-debit-bounds-v1"
    )
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    planning_only: Literal[True] = True
    basis: Literal["ALL_REGISTERED_OUTFLOWS_ASSIGNED_TO_EACH_SOURCE_UPPER_BOUND"] = (
        "ALL_REGISTERED_OUTFLOWS_ASSIGNED_TO_EACH_SOURCE_UPPER_BOUND"
    )
    account_allocation_is_exact: Literal[False] = False
    future_income_credited_cents: Literal[0] = 0
    future_principal_credited_cents: Literal[0] = 0
    borrowed_other_account_cash_cents: Literal[0] = 0
    scope: Literal["REGISTERED_CURRENT_SCOPE_365_DAYS_ONLY"] = (
        "REGISTERED_CURRENT_SCOPE_365_DAYS_ONLY"
    )
    user_id: UUID
    as_of: datetime
    timezone: Literal["UTC", "Asia/Shanghai"]
    original_effect_hash: Digest
    context_hash: Digest
    validation_hash: Digest
    projected_snapshot_hash: Digest | None
    projection_input_hash: Digest
    projection_result_hash: Digest
    original_annual_boundary_hash: Digest
    full_annual_boundary_hash: Digest | None
    expected_day_count: Literal[366] = 366
    expected_phase_count: Literal[1098] = 1098
    observed_original_phase_count: StrictInt
    observed_full_phase_count: StrictInt
    registered_source_account_ids: list[UUID]
    original_source_check_count: StrictInt
    registered_full_policy_version_ids: list[UUID]
    source_evidence_ids: list[UUID]
    maximum_cumulative_registered_outflows_cents: MoneyCents | None
    accounts: list[FullAccountDebitBound]
    status: Literal["PASSED", "BLOCKED", "UNKNOWN"]
    reasons: list[str]
    proof_hash: Digest

    @model_validator(mode="after")
    def bind_entire_proof(self) -> Self:
        if self.proof_hash != configuration_hash(
            self.model_dump(mode="json", exclude={"proof_hash"})
        ):
            raise ValueError("The complete account-bound proof hash must match")
        if self.registered_source_account_ids != [row.account_id for row in self.accounts]:
            raise ValueError("Account rows must preserve the entire registered denominator")
        if self.status == "PASSED" and (
            self.reasons
            or any(row.status != "PASSED" for row in self.accounts)
            or self.observed_original_phase_count != 1098
            or self.observed_full_phase_count != 1098
        ):
            raise ValueError("A passed bound requires complete sources and all account bounds")
        return self


def _hash(value: Any) -> str:
    return configuration_hash(value.model_dump(mode="json"))


def derive_full_account_debit_bounds(
    effect: ExecutionEffect,
    context: ExecutionContext,
    validation: ExecutionValidation,
    projection_input: FullProtectionProjectionInput,
    projection: FullProtectionProjectionResult,
) -> FullAccountDebitBoundsProof:
    """Linear derivation only; all debits conservatively hit every periodic source.

    Raw claims and exposure name overlapping reservations, so their per-account
    maximum is retained. Goal cash is separately unavailable; any resulting
    overlap is a conservative extra deduction, never a claim release.
    """
    first = context.snapshot.as_of.astimezone(ZoneInfo(context.snapshot.timezone)).date()
    curve = projection.full_annual_projection
    states = {row.policy_id: row for row in projection.policy_states}
    source_ids = {row.account_id for row in projection.source_account_checks}
    for row in projection_input.policies:
        if row.template_name == "PeriodicTransferPolicy" and (
            row.policy_id not in states or states[row.policy_id].state in {"INCLUDED", "UNKNOWN"}
        ):
            try:
                source_ids.add(UUID(str(row.configuration["source_account_id"])))
            except (KeyError, TypeError, ValueError):
                pass  # The malformed source produces UNKNOWN below, never a zero bound.
    identities = sorted(source_ids)
    values: dict[str, Any] = {
        "user_id": context.user_id,
        "as_of": context.snapshot.as_of,
        "timezone": context.snapshot.timezone,
        "original_effect_hash": execution_effect_hash(effect),
        "context_hash": _hash(context),
        "validation_hash": _hash(validation),
        "projected_snapshot_hash": _hash(validation.projected_snapshot)
        if validation.projected_snapshot
        else None,
        "projection_input_hash": _hash(projection_input),
        "projection_result_hash": _hash(projection),
        "original_annual_boundary_hash": projection.original_annual_projection.boundary_hash,
        "full_annual_boundary_hash": curve.boundary_hash if curve else None,
        "observed_original_phase_count": len(
            projection.original_annual_projection.calculation_trace
        ),
        "observed_full_phase_count": len(curve.calculation_trace) if curve else 0,
        "registered_source_account_ids": identities,
        "original_source_check_count": len(projection.source_account_checks),
        "registered_full_policy_version_ids": sorted(
            row.version_id for row in projection_input.policies
        ),
        "source_evidence_ids": sorted(
            {identity for row in projection_input.policies for identity in row.evidence_ids}
            | {
                identity
                for row in projection_input.snapshot.cash_accounts
                for identity in row.evidence_ids
            }
        ),
    }

    def finish(
        status: Literal["PASSED", "BLOCKED", "UNKNOWN"],
        reasons: list[str],
        accounts: list[FullAccountDebitBound],
        total: int | None,
    ) -> FullAccountDebitBoundsProof:
        raw = FullAccountDebitBoundsProof.model_construct(
            **values,
            status=status,
            reasons=sorted(set(reasons)),
            accounts=accounts,
            maximum_cumulative_registered_outflows_cents=total,
            proof_hash="0" * 64,
        ).model_dump(mode="json", exclude={"proof_hash"})
        return FullAccountDebitBoundsProof.model_validate_json(
            json.dumps({**raw, "proof_hash": configuration_hash(raw)})
        )

    def unknown(reason: str) -> FullAccountDebitBoundsProof:
        return finish(
            "UNKNOWN",
            [reason],
            [
                FullAccountDebitBound(
                    account_id=identity,
                    status="UNKNOWN",
                    actual_projected_cash_cents=None,
                    goal_owned_cash_cents=None,
                    other_claims_cents=None,
                    initial_free_cash_cents=None,
                    maximum_cumulative_registered_outflows_cents=None,
                    minimum_account_cash_lower_bound_cents=None,
                    daily_bounds=[],
                    reasons=[reason],
                )
                for identity in identities
            ],
            None,
        )

    try:
        effect = ExecutionEffect.model_validate_json(effect.model_dump_json())
        context = ExecutionContext.model_validate_json(context.model_dump_json())
        validation = ExecutionValidation.model_validate_json(validation.model_dump_json())
        projection_input = FullProtectionProjectionInput.model_validate_json(
            projection_input.model_dump_json()
        )
        projection = FullProtectionProjectionResult.model_validate_json(
            projection.model_dump_json()
        )
        projected = validation.projected_snapshot
        if (
            context.user_id != effect.user_id
            or validation.effect_hash != execution_effect_hash(effect)
            or validation.status not in {"READY", "CONFIRMATION_REQUIRED"}
            or projected is None
            or context.source_issues
            or context.snapshot.source_issues
            or projected.source_issues
            or not effect.valid_from <= context.snapshot.as_of < effect.expires_at
            or (projected.as_of, projected.timezone)
            != (context.snapshot.as_of, context.snapshot.timezone)
        ):
            return unknown("ORIGINAL_EXECUTION_INPUT_NOT_VERIFIED")
        if (
            projection_input.snapshot != projected
            or projection_input.boundary_versions != context.versions
            or projection_input.positions != validation.projected_positions
            or projection_input.boundary_products != context.boundary_products
            or projection_input.reserved_cash_by_account != context.reserved_cash_by_account
            or projection.input_hash != _hash(projection_input)
        ):
            return unknown("SAME_INVOCATION_PROJECTION_INPUT_BINDING_MISMATCH")
        curve = projection.full_annual_projection
        original = projection.original_annual_projection
        if (
            projection.status == "UNKNOWN"
            or curve is None
            or not projection.full_obligations_complete_within_registered_current_scope
            or not projection_input.full_source_inventory_complete
            or projection_input.full_source_issues
            or original.status == "INSUFFICIENT_EVIDENCE"
            or curve.status == "INSUFFICIENT_EVIDENCE"
            or len(original.calculation_trace) != 1098
            or len(curve.calculation_trace) != 1098
            or original.algorithm_version != "strict-cash-boundary-v1"
            or curve.algorithm_version != "registered-full-protection-v1"
            or curve.boundary_hash
            != configuration_hash(
                {
                    "algorithm": "registered-full-protection-v1",
                    "original_annual_hash": original.boundary_hash,
                    "input": projection_input.model_dump(mode="json"),
                }
            )
        ):
            return unknown("REGISTERED_FULL_CURVE_COVERAGE_NOT_PROVEN")
        if (
            len(states) != len(projection.policy_states)
            or set(states) != {row.policy_id for row in projection_input.policies}
            or len({row.policy_id for row in projection_input.policies})
            != len(projection_input.policies)
            or len(set(values["registered_full_policy_version_ids"]))
            != len(projection_input.policies)
        ):
            return unknown("FULL_POLICY_STATE_DENOMINATOR_MISMATCH")
        for source in projection_input.policies:
            canonical = validate_full_configuration(source.template_name, source.configuration)
            state = states[source.policy_id]
            if (
                configuration_hash(canonical) != source.content_hash
                or source.confirmation.get("accepted") is not True
                or source.confirmation.get("reviewed_hash") != source.content_hash
                or state.version_id != source.version_id
                or state.template_name != source.template_name
                or state.state == "UNKNOWN"
            ):
                return unknown("FULL_ORIGINAL_POLICY_SOURCE_NOT_PROVEN")
        accounts_by_id = {row.account_id: row for row in projected.cash_accounts}
        if len(accounts_by_id) != len(projected.cash_accounts) or any(
            row.observed_at > projected.as_of for row in projected.cash_accounts
        ):
            return unknown("ACTUAL_ACCOUNT_INVENTORY_NOT_PROVEN")
        owned: dict[UUID, int] = {}
        goals = {row.goal_id: row for row in projected.goals}
        if len(goals) != len(projected.goals):
            return unknown("ACTUAL_GOAL_OWNERSHIP_DENOMINATOR_NOT_PROVEN")
        for goal in projected.goals:
            if goal.allocated_cents and (
                goal.account_id not in accounts_by_id
                or accounts_by_id[goal.account_id].account_type == "CREDIT_CARD"
            ):
                return unknown("GOAL_OWNERSHIP_ACCOUNT_NOT_PROVEN")
            if goal.account_id is not None:
                owned[goal.account_id] = owned.get(goal.account_id, 0) + goal.cash_owned_cents
        for residual in projected.unassigned_goal_cash:
            if (
                residual.account_id not in accounts_by_id
                or accounts_by_id[residual.account_id].account_type != "GOAL"
            ):
                return unknown("UNASSIGNED_GOAL_ACCOUNT_NOT_PROVEN")
            owned[residual.account_id] = owned.get(residual.account_id, 0) + residual.amount_cents
        if any(
            identity not in accounts_by_id or amount > accounts_by_id[identity].balance_cents
            for identity, amount in owned.items()
        ):
            return unknown("GOAL_OWNERSHIP_EXCEEDS_ACTUAL_ACCOUNT")
        claims = dict(context.reserved_cash_by_account)
        goal_claims = dict(context.reserved_goal_cash_by_goal)
        if context.exposure is not None:
            # The original fresh importer binds its durable observation to ALL
            # current row digests and financial watermarks. An unchanged fact
            # keeps its original clock; no evidence/hash/clock is rewritten here.
            if context.exposure.as_of > context.snapshot.as_of:
                return unknown("OTHER_EXPOSURE_CLOCK_NOT_PROVEN")
            if (
                context.exposure.as_of < context.snapshot.as_of
                and not context.exposure.evidence_ids
            ):
                return unknown("OTHER_EXPOSURE_CURRENT_ROW_PROOF_MISSING")
            for identity, amount in context.exposure.reserved_cash_by_account.items():
                claims[identity] = max(claims.get(identity, 0), amount)
            for identity, amount in context.exposure.reserved_goal_cash_by_goal.items():
                goal_claims[identity] = max(goal_claims.get(identity, 0), amount)
        if not set(claims) <= set(accounts_by_id):
            return unknown("OTHER_CLAIM_ACCOUNT_NOT_PROVEN")
        grouped_claims: dict[UUID, int] = {}
        for identity, amount in goal_claims.items():
            claimed_goal = goals.get(identity)
            if (
                claimed_goal is None
                or claimed_goal.account_id is None
                or amount > claimed_goal.cash_owned_cents
            ):
                return unknown("OTHER_GOAL_CLAIM_OWNERSHIP_NOT_PROVEN")
            grouped_claims[claimed_goal.account_id] = (
                grouped_claims.get(claimed_goal.account_id, 0) + amount
            )
        if any(amount > claims.get(identity, 0) for identity, amount in grouped_claims.items()):
            return unknown("OTHER_GOAL_CLAIM_CASH_DENOMINATOR_NOT_PROVEN")
        occurrences = {row.occurrence_id: row for row in projection.occurrences}
        checks = {row.account_id: row for row in projection.source_account_checks}
        expected_sources = {
            row.source_account_id
            for row in projection.occurrences
            if row.kind == "PERIODIC_TRANSFER"
        }
        if (
            len(occurrences) != len(projection.occurrences)
            or None in expected_sources
            or set(checks) != expected_sources
            or len(checks) != len(projection.source_account_checks)
            or not expected_sources <= set(identities)
        ):
            return unknown("PERIODIC_SOURCE_DENOMINATOR_NOT_PROVEN")
        for identity in identities:
            account = accounts_by_id.get(identity)
            check = checks.get(identity)
            if account is None or account.account_type != "CASH" or check is None:
                return unknown("ACTUAL_PERIODIC_SOURCE_ACCOUNT_NOT_PROVEN")
            periodic = sum(
                row.conservative_unpaid_cents
                for row in projection.occurrences
                if row.source_account_id == identity
            )
            if (
                check.actual_cash_cents != account.balance_cents
                or check.goal_owned_cash_cents != owned.get(identity, 0)
                or check.reserved_cash_cents
                != projection_input.reserved_cash_by_account.get(identity, 0)
                or check.registered_periodic_required_cents != periodic
            ):
                return unknown("ORIGINAL_SOURCE_CHECK_BASIS_MISMATCH")
        cumulative = 0
        paid_full = 0
        daily: list[tuple[int, date, int, int]] = []
        full_due: dict[date, int] = {}
        for occurrence in projection.occurrences:
            when = occurrence.hypothetical_payment_date
            full_due[when] = full_due.get(when, 0) + occurrence.conservative_unpaid_cents
        principal_due: dict[date, list[UUID]] = {}
        position_amounts = {
            row.position_id: row.principal_cents for row in projection_input.positions
        }
        if len(position_amounts) != len(projection_input.positions):
            return unknown("ORIGINAL_POSITION_DENOMINATOR_NOT_PROVEN")
        for position in projection_input.positions:
            when_available = position.principal_available_at
            if (
                position.status not in {"REDEEMED", "UNKNOWN"}
                and when_available is not None
                and when_available > projected.as_of
            ):
                when = when_available.astimezone(ZoneInfo(projected.timezone)).date()
                principal_due.setdefault(when, []).append(position.position_id)
        original_cash = sum(
            row.balance_cents
            for row in projected.cash_accounts
            if row.account_type != "CREDIT_CARD"
        )
        for day in range(366):
            expected_date = first + timedelta(days=day)
            group = curve.calculation_trace[day * 3 : day * 3 + 3]
            baseline = original.calculation_trace[day * 3 : day * 3 + 3]
            for index, phase in enumerate(("BEFORE_PAYMENT", "AFTER_PAYMENT", "AFTER_PRINCIPAL")):
                point, old = group[index], baseline[index]
                if (point.day, point.date, point.phase) != (day, expected_date, phase) or (
                    old.day,
                    old.date,
                    old.phase,
                ) != (day, expected_date, phase):
                    return unknown("STRICT_PHASE_DAY_COVERAGE_NOT_PROVEN")
                if point.margin_cents != point.cash_cents - sum(
                    point.protected_cents_by_reason.values()
                ) or old.margin_cents != old.cash_cents - sum(
                    old.protected_cents_by_reason.values()
                ):
                    return unknown("CURVE_CASH_MARGIN_IDENTITY_MISMATCH")
            if baseline[0].cash_cents != original_cash:
                return unknown("UNREGISTERED_FUTURE_CASH_CREDIT")
            due = full_due.get(expected_date, 0)
            if group[0].cash_cents != baseline[0].cash_cents - paid_full:
                return unknown("FULL_ORIGINAL_CUMULATIVE_CASH_MISMATCH")
            paid_full += due
            if any(
                group[index].cash_cents != baseline[index].cash_cents - paid_full
                for index in (1, 2)
            ):
                return unknown("FULL_REGISTERED_PAYMENT_CASH_MISMATCH")
            debit = group[0].cash_cents - group[1].cash_cents
            if debit < 0 or baseline[0].cash_cents < baseline[1].cash_cents:
                return unknown("INVALID_AFTER_PAYMENT_CASH_DELTA")
            arriving = sorted(principal_due.get(expected_date, []))
            if (
                baseline[2].principal_position_ids != arriving
                or group[2].principal_position_ids != arriving
            ):
                return unknown("ORIGINAL_PRINCIPAL_PHASE_IDENTITY_MISMATCH")
            principal = sum(position_amounts[identity] for identity in arriving)
            if (
                baseline[2].cash_cents - baseline[1].cash_cents != principal
                or group[2].cash_cents - group[1].cash_cents != principal
            ):
                return unknown("ORIGINAL_PRINCIPAL_PHASE_CASH_MISMATCH")
            original_cash = baseline[2].cash_cents
            cumulative += debit
            daily.append((day, expected_date, debit, cumulative))
        if paid_full != sum(row.conservative_unpaid_cents for row in projection.occurrences):
            return unknown("REGISTERED_OCCURRENCE_DATE_COVERAGE_NOT_PROVEN")
        rows: list[FullAccountDebitBound] = []
        blocked: list[str] = []
        for identity in identities:
            cash = accounts_by_id[identity].balance_cents
            goal_cash, reserved = owned.get(identity, 0), claims.get(identity, 0)
            initial = cash - goal_cash - reserved
            minimum = initial - cumulative
            reasons = ["REGISTERED_ACCOUNT_DEBIT_LOWER_BOUND_NEGATIVE"] if minimum < 0 else []
            blocked.extend(reasons)
            rows.append(
                FullAccountDebitBound(
                    account_id=identity,
                    status="BLOCKED" if reasons else "PASSED",
                    actual_projected_cash_cents=cash,
                    goal_owned_cash_cents=goal_cash,
                    other_claims_cents=reserved,
                    initial_free_cash_cents=initial,
                    maximum_cumulative_registered_outflows_cents=cumulative,
                    minimum_account_cash_lower_bound_cents=minimum,
                    reasons=reasons,
                    daily_bounds=[
                        FullAccountDailyDebitBound(
                            day=day,
                            date=when,
                            registered_outflow_cents=debit,
                            cumulative_registered_outflows_cents=total,
                            account_cash_lower_bound_cents=initial - total,
                        )
                        for day, when, debit, total in daily
                    ],
                )
            )
        return finish("BLOCKED" if blocked else "PASSED", blocked, rows, cumulative)
    except (ValueError, TypeError, OverflowError, KeyError):
        return unknown("REGISTERED_ACCOUNT_BOUND_INPUT_INVALID")


def validate_full_account_debit_bounds_proof(
    proof: FullAccountDebitBoundsProof,
    effect: ExecutionEffect,
    context: ExecutionContext,
    validation: ExecutionValidation,
    projection_input: FullProtectionProjectionInput,
    projection: FullProtectionProjectionResult,
) -> FullAccountDebitBoundsProof:
    """Exact same-input re-derivation; no stale or relabelled proof is consumed."""
    proof = FullAccountDebitBoundsProof.model_validate_json(proof.model_dump_json())
    expected = derive_full_account_debit_bounds(
        effect, context, validation, projection_input, projection
    )
    if proof != expected:
        raise ValueError("Account bounds do not bind this actual same-invocation projection")
    return expected
