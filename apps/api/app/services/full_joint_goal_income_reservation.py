"""Current Joint-only income source bridge, without changing original requests.

The caller holds the original user lock and supplies this invocation's fresh
Joint proof. The original reservation, cash and bank checks remain mandatory.
No amount, use, evidence, claim or historical hash is written here.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from app.db.full_models import FullPolicy, FullPolicyVersion
from app.db.models import ActionPlan, EvidenceItem, Goal, Policy, PolicyVersion
from app.domain.execution import execution_effect_hash
from app.domain.full_dynamic_goal_execution import (
    FullDynamicGoalProof,
    derive_full_dynamic_goal_proof,
    native_income_original_matches,
)
from app.domain.full_joint_goal_execution import FullJointGoalChild
from app.domain.income_ledger import LEDGER_SOURCE, IncomeLedger
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch
from app.services.full_joint_goal_execution_guards import (
    _ordered_current,
    read_original_joint_child_request,
)
from app.services.full_joint_goal_execution_store import (
    child_binding,
    error,
    read_joint_consent,
    read_joint_plan_original,
    verify_original_joint_child,
)
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import _now
from sqlalchemy import select
from sqlalchemy.orm import Session

MAX_LEDGER_LINEAGE = 10000


def _original_ledger(row: EvidenceItem, user_id: UUID, now: datetime) -> IncomeLedger:
    if (
        row.user_id != user_id
        or row.source_type != LEDGER_SOURCE
        or row.evidence_level != "BANK_CONFIRMED"
        or row.status not in {"VALID", "SUPERSEDED"}
        or row.content_hash != configuration_hash(row.content)
        or row.observed_at > now
        or row.valid_from > now
        or row.valid_to is not None
        and now >= row.valid_to
    ):
        raise error("JOINT_INCOME_ORIGINAL_LINEAGE_NOT_VERIFIED")
    try:
        ledger = IncomeLedger.model_validate_json(json.dumps(row.content))
    except (ValueError, TypeError) as cause:
        raise error("JOINT_NATIVE_V2_INCOME_ORIGINAL_REQUIRED") from cause
    if (
        row.content.get("protocol") != "new-funds-ledger-v2"
        or row.content.get("simulation") is not True
        or row.content.get("complete") is not True
        or ledger.user_id != user_id
        or ledger.as_of > row.observed_at
    ):
        raise error("JOINT_INCOME_ORIGINAL_OWNER_OR_CLOCK_DIFFERS")
    return ledger


def _prepared_ledger(
    session: Session, action: ActionPlan, current: EvidenceItem, now: datetime
) -> IncomeLedger:
    reference = action.request.get("income_evidence")
    if not isinstance(reference, dict) or set(reference) != {"id", "hash"}:
        raise error("JOINT_PREPARED_INCOME_REFERENCE_REQUIRED")
    try:
        target = UUID(reference["id"])
    except (ValueError, TypeError, AttributeError) as cause:
        raise error("JOINT_PREPARED_INCOME_REFERENCE_INVALID") from cause
    seen: set[UUID] = set()
    row: EvidenceItem | None = current
    latest = now
    for _ in range(MAX_LEDGER_LINEAGE):
        if row is None or row.id in seen or row.observed_at > latest:
            break
        ledger = _original_ledger(row, action.user_id, now)
        if (row.id == current.id and row.status != "VALID") or (
            row.id != current.id and row.status != "SUPERSEDED"
        ):
            break
        if row.id == target:
            if reference["hash"] != row.content_hash:
                raise error("JOINT_PREPARED_INCOME_ORIGINAL_HASH_DIFFERS")
            return ledger
        seen.add(row.id)
        latest = row.observed_at
        row = session.get(EvidenceItem, row.supersedes_id) if row.supersedes_id else None
    raise error("JOINT_PREPARED_INCOME_LINEAGE_MISSING_OR_OVER_CAPACITY")


def validate_current_joint_income_reservation(
    session: Session, action: ActionPlan, proof: FullDynamicGoalProof, now: datetime
) -> None:
    """Permit only verified original fixed uses against the actual current ledger.

    Returning is source validation only. The original reserve_income still
    checks exact available fragments and records the action's actual claim.
    """
    now = _now(now)
    body = read_original_joint_child_request(action)
    _, plan, _, epoch = read_joint_plan_original(session, action.user_id, body.plan_id, now)
    child = plan.children[body.child_number - 1]
    opened = current_audit_epoch(session, action.user_id)
    if (
        opened is None
        or opened.id != epoch.id
        or epoch.status != "OPEN"
        or not plan.prepared_at <= now < plan.expires_at
        or body != child_binding(plan, body.child_number)
        or action.id != child.action_id
        or action.status not in {"PLANNED", "AUTHORIZED", "SUBMITTED"}
        or action.request.get("execution") != child.command.model_dump(mode="json")
        or not isinstance(proof, FullDynamicGoalProof)
        or proof.status != "VERIFIED_RANGE"
        or proof.reasons
        or (proof.user_id, proof.goal_id, proof.epoch_id, proof.as_of, proof.effect_hash)
        != (
            action.user_id,
            child.goal_id,
            plan.epoch_id,
            now,
            execution_effect_hash(child.command.effect),
        )
        or not proof.inputs.context.requires_confirmation
        or proof.inputs.own_effect is not None
        and proof.inputs.own_effect != child.command.effect
        or proof.model_evidence_id != child.original_model_evidence_id
        or proof.model_evidence_hash != child.original_model_evidence_hash
        or proof.inputs.request.idempotency_key != child.bank_idempotency_key
        or proof.inputs.request.goal_id != child.goal_id
        or proof.inputs.request.expected_policy_version_id != child.command.effect.policy_version_id
    ):
        raise error("JOINT_CURRENT_FIXED_INCOME_PROOF_BINDING_DIFFERS")
    verify_original_joint_child(session, plan, body.child_number, action, now)
    if read_joint_consent(session, plan, now, current=True) is None:
        raise error("JOINT_CURRENT_WHOLE_USER_CONSENT_REQUIRED_FOR_INCOME")
    _ordered_current(session, plan, body.child_number, now)
    full = session.get(FullPolicy, plan.inputs.request.full_policy_id)
    version = session.get(FullPolicyVersion, plan.inputs.request.expected_full_policy_version_id)
    goal = session.get(Goal, child.goal_id)
    mvp = session.get(PolicyVersion, child.command.effect.policy_version_id)
    policy = session.get(Policy, child.command.effect.policy_id)
    latest = session.scalar(
        select(FullPolicyVersion.id)
        .where(FullPolicyVersion.policy_id == plan.inputs.request.full_policy_id)
        .order_by(FullPolicyVersion.version_number.desc())
        .limit(1)
    )
    if (
        full is None
        or version is None
        or goal is None
        or mvp is None
        or policy is None
        or (full.user_id, full.epoch_id, full.status) != (action.user_id, plan.epoch_id, "ACTIVE")
        or version.user_id != action.user_id
        or version.policy_id != full.id
        or latest != version.id
        or version.content_hash != plan.inputs.scope.current_version.content_hash
        or version.configuration != plan.inputs.scope.current_version.configuration
        or goal.user_id != action.user_id
        or goal.policy_version_id != mvp.id
        or goal.policy_id != policy.id
        or mvp.user_id != action.user_id
        or policy.user_id != action.user_id
        or policy.status != "ACTIVE"
        or mvp.content_hash != configuration_hash(mvp.configuration)
        or mvp.valid_from is None
        or not mvp.valid_from <= now
        or mvp.valid_until is not None
        and now >= mvp.valid_until
    ):
        raise error("JOINT_CURRENT_SCOPE_ORIGINAL_GOAL_PERMISSION_CHANGED")
    state = read_income_state(session, action.user_id, now)
    original = session.get(EvidenceItem, state.evidence_id)
    model = session.get(EvidenceItem, proof.model_evidence_id)
    if (
        original is None
        or original.status != "VALID"
        or original.content_hash != state.evidence_hash
        or _original_ledger(original, action.user_id, now) != state.ledger
        or (proof.inputs.income_evidence_id, proof.inputs.income_evidence_hash)
        != (state.evidence_id, state.evidence_hash)
        or not native_income_original_matches(original.content, proof.inputs.income)
        or model is None
        or model.user_id != action.user_id
        or model.status != "VALID"
        or model.evidence_level != "USER_CONFIRMED_POLICY"
        or model.source_type != "FULL_GOAL_MODEL_V1"
        or model.source_ref != str(child.goal_id)
        or model.content != proof.inputs.model_original
        or model.content_hash != proof.model_evidence_hash
        or model.content_hash != configuration_hash(model.content)
        or model.observed_at > now
        or model.valid_from > now
        or model.valid_to is not None
        and now >= model.valid_to
    ):
        raise error("JOINT_CURRENT_ACTUAL_INCOME_OR_MODEL_SOURCE_DIFFERS")
    rebuilt = derive_full_dynamic_goal_proof(proof.inputs, child.command.effect)
    if rebuilt.status != "VERIFIED_RANGE" or rebuilt != proof:
        raise error("JOINT_CURRENT_FIXED_INCOME_PROOF_MATH_DIFFERS")
    for ref in proof.inputs.source_refs:
        evidence = session.get(EvidenceItem, ref.evidence_id)
        if (
            evidence is None
            or evidence.user_id != action.user_id
            or evidence.status != "VALID"
            or evidence.content_hash != ref.content_hash
            or configuration_hash(evidence.content) != ref.content_hash
            or evidence.observed_at > now
            or evidence.valid_from > now
            or evidence.valid_to is not None
            and now >= evidence.valid_to
        ):
            raise error("JOINT_CURRENT_PROOF_ORIGINAL_SOURCE_MISSING_OR_DIRTY")
    previous = _prepared_ledger(session, action, original, now)
    _validate_ledger_progress(previous, state.ledger, plan.children, body.child_number)


def _validate_ledger_progress(
    previous: IncomeLedger,
    current_ledger: IncomeLedger,
    children: Sequence[FullJointGoalChild],
    number: int,
) -> None:
    """Integer source continuity only; original receipts are verified by the caller."""
    previous = IncomeLedger.model_validate(previous.model_dump())
    current_ledger = IncomeLedger.model_validate(current_ledger.model_dump())
    if (
        not 1 <= number <= len(children) <= 8
        or [row.child_number for row in children] != list(range(1, len(children) + 1))
        or len({row.action_id for row in children}) != len(children)
        or previous.user_id != current_ledger.user_id
        or current_ledger.as_of < previous.as_of
    ):
        raise error("JOINT_COMPLETE_FIXED_CHILD_OR_LEDGER_IDENTITY_DIFFERS")
    child = children[number - 1]
    if any(row.command.effect.user_id != current_ledger.user_id for row in children):
        raise error("JOINT_COMPLETE_FIXED_CHILD_OR_LEDGER_IDENTITY_DIFFERS")
    old_origins = {row.origin_transaction_id: row for row in previous.origins}
    origins = {row.origin_transaction_id: row for row in current_ledger.origins}
    fragments = {row.fragment_id: row for row in current_ledger.fragments}
    before = {row.fragment_id: row for row in previous.fragments}
    commands = {row.action_id: row for row in current_ledger.reservations}
    old_commands = {row.action_id: row for row in previous.reservations}
    if (
        previous.scope_account_ids != current_ledger.scope_account_ids
        or any(origins.get(identity) != value for identity, value in old_origins.items())
        or child.action_id in commands
        or any(row.action_id in commands for row in children[number:])
    ):
        raise error("JOINT_CURRENT_INCOME_SCOPE_OR_SIBLING_CLAIM_DIFFERS")
    committed_since_prepare: dict[UUID, int] = {}
    for earlier in children[: number - 1]:
        command = commands.get(earlier.action_id)
        if (
            command is None
            or command.state != "COMMITTED"
            or command.operation != "ALLOCATE_GOAL"
            or command.destination_account_id is not None
            or sorted(command.uses, key=lambda row: str(row.fragment_id))
            != sorted(earlier.command.effect.income_uses, key=lambda row: str(row.fragment_id))
        ):
            raise error("JOINT_PRIOR_VERIFIED_RECEIPT_INCOME_COMMIT_DIFFERS")
        old_command = old_commands.get(earlier.action_id)
        if old_command is not None and old_command.state == "COMMITTED":
            if old_command != command:
                raise error("JOINT_ORIGINAL_COMMITTED_SOURCE_USES_CHANGED")
            continue
        for use in command.uses:
            committed_since_prepare[use.fragment_id] = (
                committed_since_prepare.get(use.fragment_id, 0) + use.amount_cents
            )
    for identity, amount in committed_since_prepare.items():
        old, current = before.get(identity), fragments.get(identity)
        if old is None or current is None or current.assigned_cents - old.assigned_cents < amount:
            raise error("JOINT_PRIOR_BANK_RECEIPT_NOT_RETAINED_IN_ASSIGNED_DENOMINATOR")
    for use in child.command.effect.income_uses:
        old, current = before.get(use.fragment_id), fragments.get(use.fragment_id)
        if (
            old is None
            or current is None
            or (old.origin_transaction_id, old.account_id)
            != (use.origin_transaction_id, use.account_id)
            or (current.origin_transaction_id, current.account_id)
            != (use.origin_transaction_id, use.account_id)
            or use.amount_cents > current.available_cents
            or current.available_cents > old.available_cents
        ):
            raise error("JOINT_CURRENT_FIXED_FRAGMENT_NOT_ORIGINALLY_AND_CURRENTLY_AVAILABLE")
