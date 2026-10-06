"""Invalidate only verified, never-submitted children of an immutable FULL portfolio.

This runs inside the caller's existing policy-command transaction and User lock.
It neither commits nor calls a bank, and never treats a FULL version as an MVP FK.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from app.db.full_models import (
    FullAssetExecutionBatch,
    FullAssetExecutionPortfolio,
    FullPolicy,
    FullPolicyVersion,
)
from app.db.models import (
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    BankOperation,
    SimulatedBankPosting,
    SimulatedBankRedemption,
)
from app.domain.execution_types import BankCommand, ExecutionContext
from app.domain.full_asset_execution import FullAssetFrozenPortfolio
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.audit_recording import audit_subject_data, record_action_transition
from app.services.decision_trace import get_decision_trace
from app.services.execution import _claims
from app.services.execution_exposure import refresh_execution_exposure
from app.services.execution_projection import verify_execution_receipt
from app.services.full_asset_execution_store import (
    read_consent_original,
    read_portfolio_original,
    verify_original_batch_action,
)
from app.services.full_policy_lifecycle import _versions
from app.services.income_ledger import _reservation_matches, read_income_state
from app.services.policy_lifecycle import (
    PolicyLifecycleError,
    _now,
    _release_unsubmitted_claims,
    _unsubmitted_command,
    _user,
)
from app.services.recovery_projection import _epochs
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

MAX_ORIGINALS = 10000


@dataclass(frozen=True)
class _OriginalRows:
    banks: list[BankOperation]
    receipts: list[ActionReceipt]
    postings: list[SimulatedBankPosting]
    redemptions: list[SimulatedBankRedemption]
    claims: list[ActionResourceReservation]

    @property
    def no_bank_effect(self) -> bool:
        return not (self.banks or self.receipts or self.postings or self.redemptions)


def _retained_action_ids(
    parent: FullAssetExecutionPortfolio, bindings: list[FullAssetExecutionBatch]
) -> set[UUID]:
    """Retain a missing binding's original UUID; this never authorizes its effect."""
    identities = {row.action_plan_id for row in bindings}
    try:
        frozen = FullAssetFrozenPortfolio.model_validate_json(json.dumps(parent.portfolio))
        if (
            frozen.portfolio_id == parent.id
            and frozen.user_id == parent.user_id
            and frozen.epoch_id == parent.epoch_id
            and frozen.portfolio_hash == parent.portfolio_hash
        ):
            identities.update(batch.action_id for batch in frozen.batches)
    except (ValueError, TypeError, KeyError):
        pass  # Malformed JSON supplies no additional trusted action identities.
    return identities


def _original_rows(session: Session, action: ActionPlan) -> _OriginalRows:
    # Do not filter by owner: an alien row targeting this identity must not be
    # hidden and subsequently converted into a zero-effect assertion.
    banks = list(
        session.scalars(
            select(BankOperation).where(
                or_(BankOperation.action_plan_id == action.id, BankOperation.id == action.id)
            )
        )
    )
    redemptions = list(
        session.scalars(
            select(SimulatedBankRedemption).where(
                or_(
                    SimulatedBankRedemption.action_plan_id == action.id,
                    SimulatedBankRedemption.id == action.id,
                )
            )
        )
    )
    operation_ids = {action.id, *(row.id for row in banks), *(row.id for row in redemptions)}
    rows = _OriginalRows(
        banks=banks,
        redemptions=redemptions,
        receipts=list(
            session.scalars(select(ActionReceipt).where(ActionReceipt.action_plan_id == action.id))
        ),
        postings=list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    or_(
                        SimulatedBankPosting.operation_id.in_(operation_ids),
                        SimulatedBankPosting.redemption_id.in_(operation_ids),
                    )
                )
            )
        ),
        claims=list(
            session.scalars(
                select(ActionResourceReservation).where(
                    ActionResourceReservation.action_plan_id == action.id
                )
            )
        ),
    )
    if any(len(items) > MAX_ORIGINALS for items in vars(rows).values()):
        raise PolicyLifecycleError("FULL_ASSET_RECHECK_UNVERIFIED", "原动作关联分母超容量", 409)
    return rows


def _verified_claim_state(
    session: Session,
    action: ActionPlan,
    command: BankCommand,
    rows: _OriginalRows,
    now: datetime,
) -> str:
    """Use the original claim constructor and original captured context, not new grants."""
    trace = get_decision_trace(session, action.user_id, action.decision_run_id, now)
    if (
        trace.completeness != "COMPLETE"
        or trace.audit_chain_status != "VALID"
        or trace.trace is None
        or trace.trace.phase != "PREPARE"
        or trace.trace.user_id != action.user_id
        or trace.trace.action_id != action.id
        or trace.trace.run_id != action.decision_run_id
        or trace.trace.inputs.get("effect") != command.effect.model_dump(mode="json")
        or trace.trace.inputs.get("action_request") != action.request
    ):
        raise ValueError("Original complete PREPARE trace does not bind this action")
    context = ExecutionContext.model_validate_json(
        json.dumps(trace.trace.inputs.get("execution_context"))
    )
    if context.user_id != action.user_id or context.snapshot.as_of != action.created_at:
        raise ValueError("Original claim context has a different owner or time")
    expected = {
        (row.resource_kind, row.resource_key): row.amount_cents
        for row in _claims(session, command.effect, context)
    }
    claims = rows.claims
    if claims:
        if (
            len({(row.resource_kind, row.resource_key) for row in claims}) != len(claims)
            or {(row.resource_kind, row.resource_key): row.amount_cents for row in claims}
            != expected
            or any(
                row.user_id != action.user_id
                or row.action_plan_id != action.id
                or row.id != uuid5(action.id, f"resource:{row.resource_kind}:{row.resource_key}")
                or not action.created_at <= row.created_at <= now
                or (
                    row.status == "RESERVED"
                    and row.resolved_at is not None
                    or row.status != "RESERVED"
                    and (row.resolved_at is None or not row.created_at <= row.resolved_at <= now)
                )
                for row in claims
            )
            or len({row.status for row in claims}) != 1
        ):
            raise ValueError("The complete original resource claims are missing or changed")
        state = claims[0].status
        if state not in {"RESERVED", "RELEASED", "CONSUMED"}:
            raise ValueError("Unknown original resource claim state")
    else:
        state = "ABSENT"
    ledger = read_income_state(session, action.user_id, now).ledger
    reservation = next((row for row in ledger.reservations if row.action_id == action.id), None)
    if reservation is not None:
        _reservation_matches(ledger, action.id, command)
        expected_state = "COMMITTED" if state == "CONSUMED" else state
        if expected_state != reservation.state:
            raise ValueError("Income and resource claims have different original outcomes")
    elif command.effect.income_uses and state != "ABSENT":
        raise ValueError("Original income source reservation is missing")
    return state


def _settled_original(
    session: Session,
    action: ActionPlan,
    rows: _OriginalRows,
    claim_state: str,
    now: datetime,
) -> bool:
    if (
        action.status not in {"SUCCEEDED", "RECONCILED"}
        or len(rows.banks) != 1
        or len(rows.receipts) != 1
        or rows.redemptions
        or not rows.postings
        or claim_state != "CONSUMED"
        or rows.banks[0].status != "SETTLED"
        or any(row.user_id != action.user_id for row in rows.banks)
        or any(row.user_id != action.user_id for row in rows.receipts)
        or any(row.user_id != action.user_id for row in rows.postings)
        or any(row.operation_id != rows.banks[0].id or row.redemption_id for row in rows.postings)
    ):
        return False
    # This validates exact original bank identity, independently conserved legs,
    # original receipt IDs/amounts/times. It never rechecks today's authorization.
    verify_execution_receipt(session, rows.banks[0], rows.receipts[0], now)
    return True


def _changed(
    policy: FullPolicy,
    version: FullPolicyVersion,
    portfolio: FullAssetFrozenPortfolio,
    now: datetime,
) -> bool:
    return (
        policy.status not in {"ACTIVE", "CONFIRMED"}
        or version.id != portfolio.original_request.expected_full_policy_version_id
        or version.content_hash != portfolio.full_policy_content_hash
        or now < version.valid_from
        or version.valid_until is not None
        and now >= version.valid_until
        or now >= portfolio.expires_at
    )


def recheck_full_asset_actions(
    session: Session,
    user_id: UUID,
    full_policy_id: UUID,
    epoch_id: UUID,
    now: datetime,
    lifecycle_command_id: UUID,
) -> tuple[list[UUID], list[UUID]]:
    """Return newly invalidated and unresolved original action identities.

    Call for a *new* FULL lifecycle command, before inserting its command row.
    The caller must skip this function on original command replay. Immutable
    parents/consents/commands and every submitted bank key remain untouched.
    """
    now = _now(now)
    _user(session, user_id)
    policy = session.get(FullPolicy, full_policy_id)
    epoch = current_audit_epoch(session, user_id)
    if (
        policy is None
        or policy.user_id != user_id
        or policy.epoch_id != epoch_id
        or policy.template_name != "AssetAuthorizationPolicy"
        or epoch is None
        or epoch.id != epoch_id
        or epoch.user_id != user_id
        or epoch.status != "OPEN"
    ):
        raise PolicyLifecycleError(
            "FULL_ASSET_RECHECK_SCOPE", "重查必须绑定实际当前原策略和周期", 409
        )
    versions = _versions(session, policy, now)
    all_versions = list(
        session.scalars(select(FullPolicyVersion).where(FullPolicyVersion.policy_id == policy.id))
    )
    if {row.id for row in all_versions} != {row.id for row in versions}:
        raise PolicyLifecycleError("FULL_ASSET_RECHECK_SCOPE", "策略版本完整归属分母不同", 409)
    known_versions = {row.id: row for row in versions}
    parents = list(
        session.scalars(
            select(FullAssetExecutionPortfolio)
            .where(
                FullAssetExecutionPortfolio.user_id == user_id,
                FullAssetExecutionPortfolio.epoch_id == epoch_id,
            )
            .order_by(FullAssetExecutionPortfolio.id)
        )
    )
    bindings = list(
        session.scalars(
            select(FullAssetExecutionBatch).where(
                or_(
                    (
                        (FullAssetExecutionBatch.user_id == user_id)
                        & (FullAssetExecutionBatch.epoch_id == epoch_id)
                    ),
                    FullAssetExecutionBatch.portfolio_id.in_([row.id for row in parents]),
                )
            )
        )
    )
    if len(parents) > MAX_ORIGINALS or len(bindings) > MAX_ORIGINALS:
        raise PolicyLifecycleError("FULL_ASSET_RECHECK_UNVERIFIED", "原组合关联分母超容量", 409)
    invalidated: set[UUID] = set()
    inflight: set[UUID] = set()
    if verify_audit_chain(session, user_id, epoch_id=epoch_id, mode="EXACT").status != "VALID":
        unresolved = {row.action_plan_id for row in bindings}
        for parent in parents:
            unresolved.update(_retained_action_ids(parent, []))
        return [], sorted(unresolved, key=str)
    handled: set[UUID] = set()
    declarations: dict[UUID, dict[str, str]] = {}
    for parent in parents:
        related = [row for row in bindings if row.portfolio_id == parent.id]
        handled.update(row.id for row in related)
        try:
            _, portfolio, batches, original_epoch = read_portfolio_original(
                session, user_id, parent.id, now
            )
            if (
                original_epoch.id != epoch_id
                or original_epoch.status != "OPEN"
                or {row.id for row in related} != {row.id for row in batches}
            ):
                raise ValueError("Original parent/batch inventory or epoch differs")
            if portfolio.original_request.full_policy_id != full_policy_id:
                continue
            old = known_versions.get(portfolio.original_request.expected_full_policy_version_id)
            if (
                old is None
                or old.content_hash != portfolio.full_policy_content_hash
                or old.configuration != portfolio.full_configuration.model_dump(mode="json")
            ):
                raise ValueError("Original FULL version/configuration does not bind this parent")
            read_consent_original(session, parent, portfolio, now, current=False)
        except (PolicyLifecycleError, ValueError, TypeError, KeyError):
            inflight.update(_retained_action_ids(parent, related))
            continue
        changed = _changed(policy, versions[-1], portfolio, now)
        for binding in batches:
            action = session.scalar(
                select(ActionPlan)
                .where(ActionPlan.id == binding.action_plan_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if action is None:
                inflight.add(binding.action_plan_id)
                continue
            try:
                verify_original_batch_action(session, portfolio, binding.batch_number, action, now)
                rows = _original_rows(session, action)
                state = _verified_claim_state(
                    session, action, portfolio.batches[binding.batch_number - 1].command, rows, now
                )
                if _settled_original(session, action, rows, state, now):
                    continue
                if (
                    not rows.no_bank_effect
                    or action.status in {"SUBMITTED", "UNKNOWN"}
                    or state == "CONSUMED"
                ):
                    inflight.add(action.id)
                    continue
                if action.status in {"INVALIDATED", "CANCELLED", "EXPIRED", "REJECTED"}:
                    if state == "RESERVED":
                        inflight.add(action.id)
                    continue
                if action.status not in {"PLANNED", "AUTHORIZED"}:
                    inflight.add(action.id)
                    continue
                command = _unsubmitted_command(session, action, now)
                if (
                    command is None
                    or command != portfolio.batches[binding.batch_number - 1].command
                ):
                    raise ValueError("Original unsubmitted command does not match frozen batch")
                if not changed:
                    continue
            except (PolicyLifecycleError, ValueError, TypeError, KeyError, AssertionError):
                inflight.add(action.id)
                continue
            # Do not swallow a mutation/audit failure after this point. The
            # caller's existing lifecycle savepoint/transaction must roll back.
            before_status, before_data = action.status, audit_subject_data(action)
            action.status = "INVALIDATED"
            released = _release_unsubmitted_claims(session, action, command, now)
            session.flush()
            record_action_transition(
                session,
                action,
                before_status,
                now,
                reason_code="FULL_ASSET_SCOPE_INVALIDATED_BEFORE_BANK_ACCEPT",
                cause_ref=f"full-policy-command:{lifecycle_command_id}",
                details={
                    "full_policy_id": str(full_policy_id),
                    "current_full_policy_version_id": str(versions[-1].id),
                    "original_full_policy_version_id": str(old.id),
                    "portfolio_id": str(parent.id),
                    "portfolio_hash": portfolio.portfolio_hash,
                    "batch_number": binding.batch_number,
                    "no_effect_status": "CONFIRMED",
                    "original_bank_operation_count": 0,
                    "original_receipt_count": 0,
                    "original_posting_count": 0,
                    "original_redemption_count": 0,
                    "original_claim_count": len(rows.claims),
                    "released_claim_ids": sorted(str(identity) for identity in released),
                    "creates_new_income": False,
                },
                before_data=before_data,
            )
            declarations[action.id] = {"action_id": str(action.id), "state": "NO_EFFECT"}
            invalidated.add(action.id)
    inflight.update(row.action_plan_id for row in bindings if row.id not in handled)
    if declarations:
        _epochs(session, user_id, now, lifecycle_command_id)
        refresh_execution_exposure(
            session, user_id, now, lifecycle_command_id, declarations=declarations
        )
    return sorted(invalidated, key=str), sorted(inflight, key=str)
