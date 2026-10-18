"""Actual RR/RO readers for all five delegated families; no client facts or money.

These readers are private server adapters. Native current sources, complete
question history, original receipts and current formal permissions are read on
every phase. An old USER whole confirmation is never loaded into AUTO facts.
"""

import json
from contextlib import nullcontext
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.db.full_models import FullPolicyVersion
from app.db.models import (
    ActionPlan,
    ActionReceipt,
    BankOperation,
    EvidenceItem,
    Goal,
    PolicyVersion,
    SimulatedBankPosting,
)
from app.domain.autonomy_types import AutonomyFacts
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import ExecutionContext, ExecutionEffect
from app.domain.full_dynamic_goal_execution import (
    FullDynamicGoalInput,
    FullDynamicGoalPrepareRequest,
    build_full_dynamic_goal_effect,
    derive_full_dynamic_goal_proof,
)
from app.domain.full_joint_goal_execution import FullJointGoalPrepareRequest
from app.domain.policy_configuration import configuration_hash
from app.domain.standing_income_goal_scope import AnyGrant, SignedIncomeGoalGrant, native_variant
from app.domain.standing_income_goal_slice import (
    SingleGoalIncomeSliceOriginal,
    build_income_goal_slice,
    derive_income_goal_modes,
)
from app.domain.zhiyu_standing_journal import accounted_day_total
from app.domain.zhiyu_standing_native import (
    AssetOriginal,
    DelegatedNativeInput,
    FamilyOriginal,
    JointGoalOriginal,
    PaymentOriginal,
    SingleGoalOriginal,
    WholeKnowledge,
    native_original_hash,
    original_effects,
    source_context_hash,
)
from app.domain.zhiyu_standing_scope import SignedStandingGrant
from app.services.action_contracts import ActionIntent, PaymentIntent, PurchaseIntent
from app.services.audit_chain import row_copy
from app.services.autonomy import _authority, _basis, _facts
from app.services.execution_context import load_execution_context
from app.services.execution_planning import plan_execution_effect
from app.services.execution_projection import verify_execution_receipt
from app.services.full_asset_mode_workflow import (
    all_receipts,
    chain,
    fresh_evaluation,
    read_owned_view,
    read_view,
    resolve_native_request,
)
from app.services.full_dynamic_goal_execution import read_full_dynamic_goal_inputs
from app.services.full_goals import read_full_goal_model
from app.services.full_joint_goal_execution import capture_full_joint_goal_execution
from app.services.full_policy_lifecycle import FullPolicyView, _read_snapshot, read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.policy_lifecycle import is_version_authorized
from app.services.question_workflow import _all_records, _heads, read_question_session
from app.services.standing_asset_residual_reader import read_current_residual_modes
from app.services.standing_owned_asset_scope import (
    owned_asset_read_scope,
    read_owned_asset_proof,
    unowned_asset_read_scope,
)
from app.services.zhiyu_orchestration import _audit_healthy
from app.services.zhiyu_standing_sources import ObservedOriginal, _maturity, read_original_sources
from app.services.zhiyu_standing_store import (
    PREFIX,
    _rows,
    original_control,
    original_grant,
    original_runs,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

Phase = Literal["PREPARE", "RESERVE", "BANK_ACCEPT"]


def maturity_reinvestment_limit(
    session: Session,
    user: UUID,
    grant: AnyGrant,
    observed: ObservedOriginal,
    now: datetime,
    frozen: DelegatedNativeInput | None = None,
) -> int | None:
    """Read the actual original receipt principal, never the whole available cash.

    The caller owns the current RR/RO snapshot. Re-read the native maturity
    command/Bank/receipt every financial phase; a signed finite grant still
    needs all normal current permissions, full worlds and original Bank gates.
    """
    if grant.scope.family != "MATURITY_REINVESTMENT":
        return None
    _read_snapshot(session)
    if not isinstance(grant, SignedStandingGrant):
        raise ValueError("Actual maturity requires its original finite asset grant")
    grant = SignedStandingGrant.model_validate_json(grant.model_dump_json())
    source = observed.source
    raw = observed.original_source.get("action")
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or source.source_kind != "SETTLED_MATURITY_RECEIPT"
        or (source.user_id, source.epoch_id) != (user, grant.scope.epoch_id)
        or grant.scope.user_id != user
        or source.original_source_hash != configuration_hash(observed.original_source)
        or not isinstance(raw, dict)
        or raw.get("user_id") != str(user)
        or raw.get("action_type") != "ASSET_MATURITY"
        or not isinstance(raw.get("id"), str)
    ):
        raise ValueError("Exact owned settled maturity source is required")
    action_id = UUID(raw["id"])
    action = session.get(ActionPlan, action_id)
    if (
        action is None
        or action.user_id != user
        or action.action_type != "ASSET_MATURITY"
        or action.status not in {"SUCCEEDED", "RECONCILED"}
    ):
        raise ValueError("Original owned maturity action is not settled")
    # This native reader verifies the original full-maturity binding, original
    # Bank/effect, actual receipt and exact settled destination/window.
    event = _maturity(session, grant.scope, action, now)
    if (
        event.proof_status != "VERIFIED"
        or event.service_receipt_verified is not True
        or event.issues
        or event.action_id != action.id
        or event.original_receipt_id is None
        or str(event.original_receipt_id) != source.original_source_id
        or event.originals != observed.original_source
        or event.originals_hash != source.original_source_hash
        or event.destination_account_id not in grant.scope.source_account_ids
        or (
            event.goal_id is not None and event.goal_id not in grant.scope.goal_ids
            or event.goal_id is None and bool(grant.scope.goal_ids)
        )
        or type(event.principal_cents) is not int
        or event.principal_cents <= 0
        or event.principal_cents != action.amount_cents
    ):
        raise ValueError("Original actual received principal binding differs")
    principal = event.principal_cents
    if frozen is not None:
        if (
            frozen.original_source != observed
            or frozen.grant != grant
            or not isinstance(frozen.native, AssetOriginal)
            or frozen.native.kind != "MATURITY_REINVESTMENT"
            or frozen.native.maturity_original != observed
            or frozen.native.portfolio.total_purchase_cents > principal
            or sum(row.amount_cents for row in original_effects(frozen.native)) > principal
            or any(
                world.native_preview.original_full_planning.planning_constraints.max_turnover_cents
                != principal
                for world in frozen.native.current_mode_evaluation.worlds
            )
        ):
            raise ValueError("Frozen whole maturity plan lost its original receipt bound")
    return principal


def observation(session: Session, user: UUID, event_key: str) -> ObservedOriginal:
    key = PREFIX + "source:" + event_key
    rows = [row for row in _rows(session, user, key) if row.content["key"] == key]
    if len(rows) != 1:
        raise ValueError("Exactly one original audited source observation required")
    payload = rows[0].content["payload"]
    if (
        set(payload) != {"protocol", "observation", "observation_hash", "native_bank_authority"}
        or payload["protocol"] != "standing-observed-original-record-v1"
        or payload["native_bank_authority"] is not False
        or payload["observation_hash"] != configuration_hash(payload["observation"])
    ):
        raise ValueError("Complete original source observation differs")
    value = ObservedOriginal.model_validate_json(json.dumps(payload["observation"]))
    if (
        value.source.event_key != event_key
        or value.source.original_source_hash != configuration_hash(value.original_source)
    ):
        raise ValueError("Original event identity or source hash differs")
    return value


def current_versions(
    session: Session, user: UUID, identities: list[UUID], now: datetime
) -> tuple[list[UUID], list[FullPolicyView]]:
    actual: list[UUID] = []
    full: list[FullPolicyView] = []
    for identity in identities:
        version = session.get(PolicyVersion, identity)
        if version is not None:
            if version.user_id != user or not is_version_authorized(session, user, identity, now):
                raise ValueError("Current exact formal MVP permission differs")
            actual.append(version.id)
        else:
            full_version = session.get(FullPolicyVersion, identity)
            if full_version is None or full_version.user_id != user:
                raise ValueError("Exact owned FULL version is missing")
            view = read_full_policy(session, user, full_version.policy_id, now)
            if (
                view.current_version.version_id != identity
                or view.effective_status != "ACTIVE"
                or not view.planning_confirmation_valid
                or view.reference_validation != "CURRENT"
            ):
                raise ValueError("Current exact FULL planning scope differs")
            actual.append(view.current_version.version_id)
            full.append(view)
    return actual, full


def parent_scope(full: list[FullPolicyView], template: str) -> FullPolicyView:
    candidates = [row for row in full if row.template_name == template]
    if len(candidates) != 1:
        raise ValueError("Exactly one current owned native FULL parent scope required")
    return candidates[0]


def goal_input(
    session: Session,
    user: UUID,
    goal_id: UUID,
    epoch: UUID,
    key: str,
    now: datetime,
    effect: ExecutionEffect | None = None,
) -> FullDynamicGoalInput:
    model = read_full_goal_model(session, user, goal_id, now)
    goal = session.get(Goal, goal_id)
    if (
        goal is None
        or goal.user_id != user
        or model.evidence_id is None
        or model.evidence_hash is None
    ):
        raise ValueError("Actual current goal and full model originals required")
    request = FullDynamicGoalPrepareRequest(
        goal_id=goal_id,
        expected_policy_version_id=goal.policy_version_id,
        expected_epoch_id=epoch,
        expected_model_evidence_id=model.evidence_id,
        expected_model_evidence_hash=model.evidence_hash,
        idempotency_key=key,
    )
    return read_full_dynamic_goal_inputs(session, user, request, now, own_effect=effect)


def goal_facts(
    session: Session, user: UUID, data: FullDynamicGoalInput, effect: ExecutionEffect, now: datetime
) -> AutonomyFacts:
    # The full native mathematical proof handles a legal amount above the old
    # nominal target; the actual formal MVP authority is independently re-read.
    basis = _basis(session, user, now)
    if effect.policy_id is None:
        raise ValueError("Exact original goal MVP policy is missing")
    authority = _authority(
        session, user, [effect.policy_id], now, expected=effect.policy_version_ids
    )
    proof = derive_full_dynamic_goal_proof(data, effect)
    validation = revalidate_execution(effect, data.context, full_dynamic_goal_proof=proof)
    return AutonomyFacts(
        user_id=user,
        as_of=now,
        action_type=effect.action_type,
        initiation="CONFIRMED_POLICY",
        authority=authority,
        effect=effect,
        validation=validation,
        source_evidence_ids=sorted(
            {
                *basis.context.sources.used,
                *authority.evidence_ids,
                *(row.evidence_id for row in data.source_refs),
            },
            key=str,
        ),
        source_issues=basis.issues,
        source_context_hash=basis.digest,
        hard_block_reasons=["BASELINE_LIQUIDITY_RISK"]
        if basis.boundary.status == "LIQUIDITY_RISK"
        else [],
    )


def cumulative_goal_facts(
    session: Session, user: UUID, data: FullDynamicGoalInput, effect: ExecutionEffect, now: datetime
) -> AutonomyFacts:
    # Selected cumulative mode uses the actual original MVP nominal target;
    # genuine current policy authority is still independently re-read.
    basis = _basis(session, user, now)
    if effect.policy_id is None:
        raise ValueError("Exact original goal MVP policy is missing")
    authority = _authority(
        session, user, [effect.policy_id], now, expected=effect.policy_version_ids
    )
    validation = revalidate_execution(effect, data.context)
    return AutonomyFacts(
        user_id=user,
        as_of=now,
        action_type=effect.action_type,
        initiation="CONFIRMED_POLICY",
        authority=authority,
        effect=effect,
        validation=validation,
        source_evidence_ids=sorted(
            {
                *basis.context.sources.used,
                *authority.evidence_ids,
                *(row.evidence_id for row in data.source_refs),
            },
            key=str,
        ),
        source_issues=basis.issues,
        source_context_hash=basis.digest,
        hard_block_reasons=["BASELINE_LIQUIDITY_RISK"]
        if basis.boundary.status == "LIQUIDITY_RISK"
        else [],
    )


def owned_native_facts(
    session: Session,
    user: UUID,
    intent: ActionIntent,
    effect: ExecutionEffect,
    context: ExecutionContext,
    now: datetime,
) -> AutonomyFacts:
    """Use exact current native own-action exclusions without reading old consent.

    The original adapter still derives formal authority/payee/source issues and
    all hard blocks from current server originals. Only its financial validation
    is recomputed against the independently loaded native context; pending own
    reservations must not be counted twice. Native confirmation bits remain.
    """
    facts = _facts(session, user, intent, now, _basis(session, user, now), effect=effect)
    if facts.confirmation is not None:
        raise ValueError("A new SYSTEM delegation cannot inherit USER once-consent")
    return facts.model_copy(
        update={
            "effect": effect,
            "action_type": effect.action_type,
            "validation": revalidate_execution(effect, context, confirmation=None),
            "confirmation_reasons": ["EXPLICIT_POLICY_PAYMENT_CONFIRMATION"]
            if context.requires_confirmation
            else [],
            "source_context_hash": configuration_hash(
                {
                    "original_facts_context": facts.source_context_hash,
                    "current_native_context": context.model_dump(mode="json"),
                }
            ),
        }
    )


def settled_originals(
    session: Session, user: UUID, effects: list[ExecutionEffect], now: datetime
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for effect in effects:
        action = session.get(ActionPlan, effect.operation_id)
        if (
            action is None
            or action.user_id != user
            or action.status not in {"SUCCEEDED", "RECONCILED"}
        ):
            break
        operations = list(
            session.scalars(
                select(BankOperation).where(
                    BankOperation.user_id == user, BankOperation.action_plan_id == action.id
                )
            )
        )
        receipts = list(
            session.scalars(
                select(ActionReceipt).where(
                    ActionReceipt.user_id == user, ActionReceipt.action_plan_id == action.id
                )
            )
        )
        if len(operations) != 1 or len(receipts) != 1:
            raise ValueError("Settled child requires one exact original bank and receipt")
        operation, receipt = operations[0], receipts[0]
        verify_execution_receipt(session, operation, receipt, now)
        postings = list(
            session.scalars(
                select(SimulatedBankPosting)
                .where(
                    SimulatedBankPosting.user_id == user,
                    SimulatedBankPosting.operation_id == operation.id,
                )
                .order_by(SimulatedBankPosting.id)
            )
        )
        result.append(
            {
                "action": row_copy(action),
                "bank": row_copy(operation),
                "receipt": row_copy(receipt),
                "postings": [row_copy(row) for row in postings],
            }
        )
    # Later successes cannot mask a missing/UNKNOWN earlier original child.
    for effect in effects[len(result) :]:
        action = session.get(ActionPlan, effect.operation_id)
        if action is not None and action.status in {"SUCCEEDED", "RECONCILED"}:
            raise ValueError("Original native child settlement order differs")
    return result


def question_originals(
    session: Session,
    user: UUID,
    now: datetime,
    owned_asset: AssetOriginal | None = None,
    accepted_question_originals: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[UUID]]:
    records = _all_records(session, user, now)
    originals = [state.model_dump(mode="json") for _, state in records]
    pending: list[UUID] = []
    for head in _heads(records):
        if head.state == "CLOSED":
            continue
        with unowned_asset_read_scope():
            current = read_question_session(session, user, head.session_id, now)
        if (
            current.effective_state != "READY_FOR_REVIEW"
            or current.current_source_fingerprint != head.source_fingerprint
        ):
            pending.append(head.session_id)
        originals.append(current.model_dump(mode="json"))
    asset_records = all_receipts(session, user)
    originals.extend(row.model_dump(mode="json") for row in asset_records)
    for identity in sorted({row.session_id for row in asset_records}, key=str):
        asset_head = chain(asset_records, identity)[-1]
        if asset_head.state == "CLOSED":
            continue
        if (
            owned_asset is not None
            and owned_asset.owned_read_proof is not None
            and owned_asset.owned_source_manifest is not None
            and owned_asset.residual_mode_evaluation is not None
            and UUID(chain(asset_records, identity)[0].original_request["body"]["full_policy_id"])
            == owned_asset.portfolio.original_request.full_policy_id
        ):
            current_asset = read_owned_view(
                session,
                user,
                identity,
                now,
                owned_asset.owned_read_proof,
                owned_asset.residual_mode_evaluation,
                owned_asset.owned_source_manifest,
                accepted_question_originals or [],
            )
        else:
            with unowned_asset_read_scope():
                current_asset = read_view(session, user, identity, now)
        originals.append(current_asset)
        if current_asset.get("effective_state") != "READY_FOR_REVIEW":
            pending.append(identity)
    return originals, sorted(set(pending), key=str)


def _payment_original(
    session: Session,
    user: UUID,
    full: FullPolicyView,
    key: str,
    now: datetime,
    original: PaymentOriginal | None,
) -> PaymentOriginal:
    from app.domain.full_payment_permissions import SOURCE
    from app.services.full_payment_permissions import _original, _verify_effect

    rows = list(
        session.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.user_id == user, EvidenceItem.source_type == SOURCE)
            .order_by(EvidenceItem.id)
            .limit(129)
        )
    )
    if len(rows) > 128:
        raise ValueError("Complete payment relation inventory exceeds explicit capacity")
    matches = []
    for row in rows:
        if row.content.get("kind") == "CONFIRM" and row.content.get("scope", {}).get(
            "full_policy_id"
        ) == str(full.policy_id):
            current = _original(
                session, user, UUID(str(row.content["command_id"])), now, replay=True
            )
            if current is not None and current.current_scope_status == "CURRENT":
                matches.append(current)
    if len(matches) != 1 or not matches[0].original.scope.auto_execute:
        raise ValueError("Exactly one original signed USER AUTO relation required")
    authorization = matches[0].original
    scope = authorization.scope
    if original is None:
        period = now.astimezone(ZoneInfo(scope.timezone)).strftime("%Y-%m")
        effect = plan_execution_effect(
            session,
            user,
            uuid5(user, key + ":payment"),
            PaymentIntent(kind="pay_recurring", policy_id=scope.original_policy_id, period=period),
            now,
        )
    else:
        effect = original.effect
    _verify_effect(session, scope, effect, now)
    context = load_execution_context(
        session, user, effect, now, own_action_id=effect.operation_id if original else None
    )
    return PaymentOriginal(
        effect=effect,
        context=context,
        original_authorization=authorization.model_dump(mode="json"),
        current_scope=scope.model_dump(mode="json"),
    )


def capture_native_snapshot(
    session: Session,
    user: UUID,
    grant_id: UUID,
    event_key: str,
    now: datetime,
    phase: Phase,
    frozen: DelegatedNativeInput | None = None,
) -> DelegatedNativeInput:
    _read_snapshot(session)
    _audit_healthy(session, user)
    grant = original_grant(session, user, grant_id)
    control = original_control(session, user, grant_id)
    runs = original_runs(session, user)
    owned = [row for row in runs if row.source.event_key == event_key]
    if len(owned) != 1 or owned[0].grant_id != grant_id:
        raise ValueError("Exact original observed event and grant required")
    observed = observation(session, user, event_key)
    current_source = [
        row
        for row in read_original_sources(session, grant.scope, observed.source.environment_id, now)
        if row.source == observed.source
    ]
    if current_source != [observed]:
        raise ValueError("Current independent source original differs or grant is inactive")
    maturity_limit = maturity_reinvestment_limit(session, user, grant, observed, now, frozen)
    versions, full = current_versions(session, user, grant.scope.policy_version_ids, now)
    key = "standing:" + event_key
    native: FamilyOriginal
    facts: list[AutonomyFacts] = []
    prefix: list[dict[str, Any]] = []
    if frozen is not None:
        prefix = settled_originals(session, user, original_effects(frozen.native), now)
    start = len(prefix)
    if grant.scope.family == "SINGLE_GOAL":
        previous = frozen.native if frozen is not None else None
        if previous is not None and not isinstance(
            previous, (SingleGoalOriginal, SingleGoalIncomeSliceOriginal)
        ):
            raise ValueError("Original native family differs")
        old_effect = (
            previous.effect
            if isinstance(previous, (SingleGoalOriginal, SingleGoalIncomeSliceOriginal))
            else None
        )
        data = goal_input(
            session, user, grant.scope.goal_ids[0], grant.scope.epoch_id, key, now, old_effect
        )
        if isinstance(grant, SignedIncomeGoalGrant):
            if previous is not None and not isinstance(previous, SingleGoalIncomeSliceOriginal):
                raise ValueError("Accepted legacy original cannot be upgraded to cumulative mode")
            effect = (
                old_effect or build_income_goal_slice(data, observed, uuid5(user, key + ":goal"))[0]
            )
            modes = derive_income_goal_modes(
                data, observed, effect, selected_mode=grant.scope.goal_mode
            )
            native = SingleGoalIncomeSliceOriginal(
                current=data, observed=observed, effect=effect, modes=modes
            )
            facts = [cumulative_goal_facts(session, user, data, effect, now)]
        else:
            if previous is not None and not isinstance(previous, SingleGoalOriginal):
                raise ValueError("Accepted cumulative original cannot become legacy dynamic")
            effect = (
                old_effect or build_full_dynamic_goal_effect(data, uuid5(user, key + ":goal"))[0]
            )
            native = SingleGoalOriginal(current=data, effect=effect)
            facts = [goal_facts(session, user, data, effect, now)]
    elif grant.scope.family == "JOINT_GOALS":
        parent = parent_scope(full, "GoalAllocationPolicy")
        if frozen is None:
            body = FullJointGoalPrepareRequest(
                full_policy_id=parent.policy_id,
                expected_full_policy_version_id=parent.current_version.version_id,
                expected_epoch_id=grant.scope.epoch_id,
                idempotency_key=key,
            )
            captured = capture_full_joint_goal_execution(
                session, user, body, now, archive=True, source_dag=True
            )
            plan = captured.preview.plan
            if plan is None or captured.preview.status != "READY_TO_REVIEW":
                raise ValueError("Complete current native joint plan is not ready")
        elif isinstance(frozen.native, JointGoalOriginal):
            plan = frozen.native.plan
        else:
            raise ValueError("Original native joint family differs")
        models: dict[UUID, tuple[UUID, str, UUID]] = {}
        for goal_id in grant.scope.goal_ids:
            model = read_full_goal_model(session, user, goal_id, now)
            goal = session.get(Goal, goal_id)
            if (
                goal is None
                or goal.user_id != user
                or model.evidence_id is None
                or model.evidence_hash is None
            ):
                raise ValueError("Complete current goal model denominator required")
            models[goal_id] = (model.evidence_id, model.evidence_hash, goal.policy_version_id)
        inputs = [
            goal_input(
                session,
                user,
                child.goal_id,
                grant.scope.epoch_id,
                key + ":" + str(child.child_number),
                now,
                child.command.effect
                if frozen and session.get(ActionPlan, child.action_id) is not None
                else None,
            )
            for child in plan.children[start:]
        ]
        for data, child in zip(inputs, plan.children[start:], strict=True):
            facts.append(goal_facts(session, user, data, child.command.effect, now))
        native = JointGoalOriginal(
            plan=plan,
            current_models=models,
            current_full_version_id=parent.current_version.version_id,
            current_full_content_hash=parent.current_version.content_hash,
            current_full_policy=parent,
            current_inputs=inputs,
        )
    elif grant.scope.family in {"ASSET_PORTFOLIO", "MATURITY_REINVESTMENT"}:
        parent = parent_scope(full, "AssetAuthorizationPolicy")
        previous_asset = frozen.native if frozen is not None else None
        if previous_asset is not None and not isinstance(previous_asset, AssetOriginal):
            raise ValueError("Original native asset family differs")
        request = (
            previous_asset.portfolio.original_request
            if isinstance(previous_asset, AssetOriginal)
            else resolve_native_request(
                session, user, parent.policy_id, grant.scope.epoch_id, key, now
            )
        )
        owned_proof = (
            read_owned_asset_proof(session, frozen, owned[0], now) if frozen is not None else None
        )
        with owned_asset_read_scope(session, owned_proof) if owned_proof else nullcontext():
            residual = None
            if isinstance(previous_asset, AssetOriginal) and owned_proof is not None:
                evaluation = previous_asset.current_mode_evaluation
                residual, owned_manifest = read_current_residual_modes(
                    session,
                    user,
                    evaluation,
                    owned_proof,
                    now,
                )
                portfolio = previous_asset.portfolio
            else:
                evaluation, owned_manifest, _ = fresh_evaluation(
                    session, user, request, now, maximum_turnover_cents=maturity_limit
                )
                matching = [
                    world for world in evaluation.worlds if world.mode == request.planning_mode
                ]
                if len(matching) != 1 or matching[0].native_preview.portfolio is None:
                    raise ValueError("Actual complete native asset world is missing")
                portfolio = matching[0].native_preview.portfolio
            remaining = portfolio.batches[start:]
            if not remaining:
                raise ValueError("Original whole portfolio is already settled")
            context = load_execution_context(
                session,
                user,
                remaining[0].command.effect,
                now,
                own_action_id=None,
            )
            if residual is not None:
                facts = [
                    child.facts
                    for world in residual.worlds
                    if world.input.original_world.mode == request.planning_mode
                    for child in world.input.current_children
                ]
                context = next(
                    world.input.current_context
                    for world in residual.worlds
                    if world.input.original_world.mode == request.planning_mode
                )
            else:
                facts = [
                    owned_native_facts(
                        session,
                        user,
                        PurchaseIntent(
                            kind="purchase_asset", policy_id=request.mvp_asset_policy_id
                        ),
                        batch.command.effect,
                        load_execution_context(
                            session,
                            user,
                            batch.command.effect,
                            now,
                            own_action_id=None,
                        ),
                        now,
                    )
                    for batch in remaining
                ]
            native = AssetOriginal(
                kind=cast(Literal["ASSET_PORTFOLIO", "MATURITY_REINVESTMENT"], grant.scope.family),
                portfolio=portfolio,
                context=context,
                current_full_content_hash=parent.current_version.content_hash,
                current_full_policy=parent,
                full_inventory_complete=True,
                remaining_batch_numbers=[batch.batch_number for batch in remaining],
                current_mode_evaluation=evaluation,
                mode_source_manifest=previous_asset.mode_source_manifest
                if isinstance(previous_asset, AssetOriginal)
                else owned_manifest,
                residual_mode_evaluation=residual,
                owned_read_proof=owned_proof,
                owned_source_manifest=owned_manifest if owned_proof else None,
                maturity_original=observed
                if grant.scope.family == "MATURITY_REINVESTMENT"
                else None,
            )
    else:
        parent = parent_scope(full, "PeriodicTransferPolicy")
        previous_payment = frozen.native if frozen is not None else None
        if previous_payment is not None and not isinstance(previous_payment, PaymentOriginal):
            raise ValueError("Original native payment family differs")
        native = _payment_original(session, user, parent, key, now, previous_payment)
        effect = native.effect
        if (
            effect.policy_id is None
            or effect.liability is None
            or effect.liability.kind != "occurrence"
        ):
            raise ValueError("Original payment policy and period are missing")
        facts = [
            owned_native_facts(
                session,
                user,
                PaymentIntent(
                    kind="pay_recurring", policy_id=effect.policy_id, period=effect.liability.period
                ),
                effect,
                native.context,
                now,
            )
        ]
    current_owned = native.owned_read_proof if isinstance(native, AssetOriginal) else None
    with owned_asset_read_scope(session, current_owned) if current_owned else nullcontext():
        protection = compute_full_annual_protection(session, user, now)
        if not protection.projection.full_obligations_complete_within_registered_current_scope:
            raise ValueError("Actual complete current FULL protection inventory is missing")
        originals, pending = question_originals(
            session,
            user,
            now,
            native if isinstance(native, AssetOriginal) else None,
            frozen.knowledge.current_question_originals if frozen else None,
        )
    day = now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    knowledge = WholeKnowledge(
        native_original_hash=native_original_hash(native),
        ordered_effect_hashes=[execution_effect_hash(row) for row in original_effects(native)],
        current_question_originals=originals,
        pending_question_ids=pending,
        source_context_hash="0" * 64,
        status="UNKNOWN" if pending else "KNOWN",
    )
    value = DelegatedNativeInput(
        protocol=native_variant(grant),
        phase=phase,
        user_id=user,
        epoch_id=grant.scope.epoch_id,
        as_of=now,
        original_source=observed,
        grant=grant,
        current_control=control,
        current_version_ids=versions,
        current_runs=runs,
        spent_today_cents=accounted_day_total(runs, user, grant.scope.epoch_id, day),
        event_accepted_total_cents=owned[0].accepted_total_cents
        if owned[0].accounting_day == day
        else 0,
        settled_prefix=start,
        original_settlements=prefix,
        native=native,
        knowledge=knowledge,
        current_facts=facts,
        full_sources=protection.full_policy_sources,
        full_source_issues=[row.code for row in protection.source_issues],
    )
    return value.model_copy(
        update={
            "knowledge": knowledge.model_copy(
                update={"source_context_hash": source_context_hash(value)}
            )
        }
    )
