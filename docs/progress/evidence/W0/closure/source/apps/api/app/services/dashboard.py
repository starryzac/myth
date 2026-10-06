"""Aggregate verified facts in the caller's read-only repeatable-read transaction."""

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from app.api.v1.accounts import account_summary
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    BankOperation,
    CreditCardBill,
    DecisionRun,
    EvidenceItem,
    ExternalBankFact,
    Goal,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    Transaction,
    User,
)
from app.domain.boundary import compute_boundary_with_details
from app.domain.policy_configuration import configuration_hash
from app.services.autonomy import assess_action
from app.services.boundary import BoundaryContext
from app.services.dashboard_helpers import current_epoch_audit
from app.services.dashboard_types import (
    AccountFactsCard,
    DashboardResponse,
    GoalOwnershipCard,
    GoalOwnershipItem,
    InterventionCard,
    ManagedAssetsCard,
    ManagedGoalAmount,
    PendingActionItem,
    PendingActionsCard,
    RecoveryProposalItem,
    RecoveryProposalsCard,
)
from app.services.execution import get_action
from app.services.financial_read import (
    finalize_financial_context,
    financial_card,
    load_verified_financial_context,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.recovery import RecoveryPreviewResponse
from app.services.recovery_receipt_integrity import verify_recovery_receipt
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import Session

PENDING = {"PLANNED", "AUTHORIZED", "SUBMITTED", "UNKNOWN"}
RECONCILE = {"SUBMITTED", "UNKNOWN"}


def _capacity(session: Session, user_id: UUID) -> None:
    """Reject oversized complete inputs before materializing any financial collections."""
    limits: list[tuple[Any, int]] = [
        (Account, 100),
        (Goal, 100),
        (CreditCardBill, 10_000),
        (AssetPosition, 10_000),
        (ActionPlan, 10_000),
        (DecisionRun, 10_000),
        (EvidenceItem, 100_000),
        (ActionReceipt, 100_000),
        (Transaction, 100_000),
        (BankOperation, 10_000),
        (SimulatedBankRedemption, 10_000),
        (ExternalBankFact, 10_000),
        (SimulatedBankPosting, 100_000),
    ]
    for model, maximum in limits:
        count = (
            session.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))
            or 0
        )
        if count > maximum:
            raise PolicyLifecycleError("INPUT_LIMIT_EXCEEDED", "首页完整事实超过读取容量", 409)
    byte_fields = [
        EvidenceItem.content,
        DecisionRun.input_snapshot,
        ActionPlan.request,
        ActionReceipt.response,
        ExternalBankFact.request,
        SimulatedBankPosting.ledger_metadata,
    ]
    total_bytes = 0
    for field in byte_fields:
        model = field.class_
        total_bytes += (
            session.scalar(
                select(func.coalesce(func.sum(func.pg_column_size(field)), 0)).where(
                    model.user_id == user_id
                )
            )
            or 0
        )
        if total_bytes > 64 * 1024 * 1024:
            raise PolicyLifecycleError("INPUT_LIMIT_EXCEEDED", "首页完整事实超过字节容量", 409)


def _pending_query(user_id: UUID) -> Any:
    receipts = (
        exists()
        .where(ActionReceipt.user_id == user_id, ActionReceipt.action_plan_id == ActionPlan.id)
        .correlate(ActionPlan)
    )
    receipt_count = (
        select(func.count())
        .select_from(ActionReceipt)
        .where(ActionReceipt.user_id == user_id, ActionReceipt.action_plan_id == ActionPlan.id)
        .correlate(ActionPlan)
        .scalar_subquery()
    )
    in_bank = (
        exists()
        .where(
            BankOperation.user_id == user_id,
            BankOperation.action_plan_id == ActionPlan.id,
            BankOperation.status.in_({"ACCEPTED", "UNKNOWN", "SETTLED"}),
            or_(BankOperation.status != "SETTLED", ~receipts),
        )
        .correlate(ActionPlan)
    )
    return select(ActionPlan).where(
        ActionPlan.user_id == user_id,
        or_(
            ActionPlan.status.in_(PENDING),
            in_bank,
            ActionPlan.status.in_({"SUCCEEDED", "RECONCILED"}) & ~receipts,
            receipt_count > 1,
        ),
    )


def _proposal_query(user_id: UUID) -> Any:
    has_action = (
        exists()
        .where(ActionPlan.user_id == user_id, ActionPlan.decision_run_id == DecisionRun.id)
        .correlate(DecisionRun)
    )
    return select(DecisionRun).where(
        DecisionRun.user_id == user_id,
        DecisionRun.trigger_type == "SAFETY_RECOVERY",
        ~has_action,
        DecisionRun.input_snapshot["preview"]["plan"]["status"].astext.in_(
            {"ASK_ONCE", "ADVISE_ONLY", "NO_SAFE_RECOVERY", "INSUFFICIENT_EVIDENCE"}
        ),
    )


def _action(
    session: Session, context: BoundaryContext, row: ActionPlan, audit_status: str
) -> PendingActionItem:
    user_id, now = context.sources.user_id, context.snapshot.as_of
    operations = list(
        session.scalars(
            select(BankOperation).where(
                BankOperation.user_id == user_id,
                BankOperation.action_plan_id == row.id,
            )
        )
    )
    receipts = list(
        session.scalars(
            select(ActionReceipt).where(
                ActionReceipt.user_id == user_id,
                ActionReceipt.action_plan_id == row.id,
            )
        )
    )
    operation = operations[0] if len(operations) == 1 else None
    receipt = receipts[0] if len(receipts) == 1 else None
    reasons: list[str] = []
    decision = None
    effect_hash = None
    amount = fee = loss = None
    verified = False
    bank_proven = len(operations) <= 1 and len(receipts) <= 1
    try:
        if not bank_proven or configuration_hash(row.request) != row.request_hash:
            raise ValueError("Conflicting original action or receipt linkage")
        if "execution" in row.request:
            original = get_action(session, user_id, row.id, now)
            effect_hash = original.effect_hash
            amount, fee, loss = (
                row.amount_cents,
                original.effect.fee_cents,
                original.effect.loss_cents,
            )
            verified = original.receipt is not None
            if operation is None and row.status not in RECONCILE:
                decision = assess_action(session, user_id, row.id, now, context=context).decision
            else:
                reasons.append("CURRENT_CLASSIFICATION_NOT_APPLICABLE")
        else:
            # Legacy recovery keeps its original bank request and never acquires a new grant.
            requests = list(
                session.scalars(
                    select(SimulatedBankRedemption).where(
                        SimulatedBankRedemption.user_id == user_id,
                        SimulatedBankRedemption.action_plan_id == row.id,
                    )
                )
            )
            if len(requests) != 1 or operation is None or requests[0].id != operation.id:
                raise ValueError("Legacy request is missing or conflicted")
            if receipt is not None:
                verify_recovery_receipt(session, requests[0], receipt, now)
                verified = True
            amount = row.amount_cents
            reasons.append("LEGACY_RECOVERY_ORIGINAL_REQUEST")
        if operation is not None and (
            operation.status in {"ACCEPTED", "UNKNOWN"}
            or operation.status == "SETTLED"
            and not verified
        ):
            reasons.append("BANK_RECONCILIATION_REQUIRED")
        if row.status in RECONCILE:
            reasons.append("BANK_RECONCILIATION_REQUIRED")
    except (PolicyLifecycleError, ValueError, TypeError, KeyError) as error:
        reasons.append(
            error.code if isinstance(error, PolicyLifecycleError) else "INVALID_EXECUTION_SOURCE"
        )
        bank_proven = False
    if audit_status != "VALID":
        reasons.append("ACTION_AUDIT_" + audit_status)
        # Incomplete provenance cannot publish a current actionable classification.
        decision = None
    return PendingActionItem(
        action_id=row.id,
        decision_run_id=row.decision_run_id,
        action_type=row.action_type,
        amount_cents=amount,
        status=row.status,
        prepared_level=row.autonomy_level,
        prepared_at=row.created_at,
        current_decision=decision,
        effect_hash=effect_hash,
        fee_cents=fee,
        loss_cents=loss,
        bank_operation_id=operation.id if operation else None,
        bank_status=operation.status if operation else None,
        bank_state_proven=bank_proven,
        receipt_id=receipt.id if receipt else None,
        receipt_status=receipt.status if receipt else None,
        receipt_verified=verified,
        audit_status=audit_status,
        reason_codes=sorted(set(reasons)),
    )


def _proposal(
    row: DecisionRun, context: BoundaryContext, audit_status: str
) -> RecoveryProposalItem:
    reasons = ["REVIEW_ORIGINAL_RECOVERY_PROPOSAL"]
    original_status = "NOT_PROVEN"
    fee = loss = None
    status = "NOT_PROVEN"
    try:
        if row.snapshot_hash != configuration_hash(row.input_snapshot):
            raise ValueError("Original recovery snapshot changed")
        preview = RecoveryPreviewResponse.model_validate_json(
            json.dumps(row.input_snapshot["preview"])
        )
        if preview.user_id != row.user_id or preview.as_of != row.as_of:
            raise ValueError("Original recovery tenant or clock differs")
        original_status = preview.plan.status
        # The proposal is historical. This read never confirms or replays its economic effect.
        proposed = preview.plan.steps or [
            candidate.action
            for candidate in preview.plan.candidates
            if candidate.decision == "ASK_ONCE" and candidate.action is not None
        ]
        # Alternatives are not additive. Precise costs require a single original proposal.
        if len(proposed) == 1:
            fee = proposed[0].quote.fee_cents
            loss = proposed[0].quote.loss_cents
        status = "REVIEW_REQUIRED"
        if context.snapshot.source_issues:
            status = "NOT_PROVEN"
            reasons.append("CURRENT_SOURCE_NOT_PROVEN")
        elif original_status == "INSUFFICIENT_EVIDENCE":
            status = "NOT_PROVEN"
        if audit_status != "VALID":
            status = "NOT_PROVEN"
            reasons.append("PROPOSAL_AUDIT_" + audit_status)
    except (TypeError, ValueError, KeyError):
        reasons.append("INVALID_RECOVERY_RUN")
    return RecoveryProposalItem(
        run_id=row.id,
        as_of=row.as_of,
        status=status,
        original_status=original_status,
        fee_cents=fee,
        loss_cents=loss,
        audit_status=audit_status,
        reason_codes=reasons,
    )


def intervention(
    pending: PendingActionsCard,
    proposals: RecoveryProposalsCard,
    *,
    sources_proven: bool,
    audit_complete: bool,
) -> InterventionCard:
    """A complete empty verified set is required before stating no intervention."""
    reconcile = [
        item
        for item in pending.items
        if item.status in RECONCILE or "BANK_RECONCILIATION_REQUIRED" in item.reason_codes
    ]
    confirm = [
        item
        for item in pending.items
        if item.current_decision is not None
        and item.current_decision.confirmation_required
        and not item.current_decision.confirmation_satisfied
    ]
    review = [item for item in proposals.items if item.status == "REVIEW_REQUIRED"]
    complete = (
        pending.list_complete
        and proposals.list_complete
        and sources_proven
        and audit_complete
        and pending.state == "PROVEN"
        and proposals.state == "PROVEN"
    )
    count = len({item.action_id for item in reconcile + confirm}) + len(review)
    reasons: list[str] = []
    status: Literal[
        "NONE", "CONFIRMATION_REQUIRED", "REVIEW_REQUIRED", "RECONCILIATION_REQUIRED", "NOT_PROVEN"
    ]
    if reconcile:
        status = "RECONCILIATION_REQUIRED"
        reasons.append("BANK_RECONCILIATION_REQUIRED")
    elif confirm:
        status = "CONFIRMATION_REQUIRED"
        reasons.append("EXACT_CONFIRMATION_REQUIRED")
    elif review:
        status = "REVIEW_REQUIRED"
        reasons.append("REVIEW_RECOVERY_PROPOSAL")
    elif not complete:
        status = "NOT_PROVEN"
        reasons.append("INCOMPLETE_CURRENT_INTERVENTION_PROOF")
    else:
        status = "NONE"
    return InterventionCard(
        status=status, known_required_count=count, complete=complete, reason_codes=reasons
    )


def get_dashboard(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    pending_limit: int = 20,
    recovery_limit: int = 10,
) -> DashboardResponse:
    if not 1 <= pending_limit <= 100 or not 1 <= recovery_limit <= 100:
        raise PolicyLifecycleError("INVALID_READ_LIMIT", "首页列表数量超出范围", 422)
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时钟必须带时区")
    now = now.astimezone(UTC)
    with session.no_autoflush:
        _capacity(session, user_id)
        user = session.get(User, user_id)
        if user is None or not user.is_simulated:
            raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
        facts = account_summary(session, user)
        context, bank_matched, exposure = load_verified_financial_context(session, user_id, now)
        pq, rq = _pending_query(user_id), _proposal_query(user_id)
        pending_total = session.scalar(select(func.count()).select_from(pq.subquery())) or 0
        recovery_total = session.scalar(select(func.count()).select_from(rq.subquery())) or 0
        action_rows = list(
            session.scalars(
                pq.order_by(
                    case((ActionPlan.status.in_(RECONCILE), 0), else_=1),
                    ActionPlan.created_at,
                    ActionPlan.id,
                ).limit(pending_limit)
            )
        )
        proposal_rows = list(
            session.scalars(
                rq.order_by(DecisionRun.as_of.desc(), DecisionRun.id).limit(recovery_limit)
            )
        )
        audit = current_epoch_audit(
            session,
            user_id,
            sorted(
                {row.decision_run_id for row in action_rows} | {row.id for row in proposal_rows}
            ),
        )
        context, issues, digest = finalize_financial_context(context, audit)
        computed = compute_boundary_with_details(
            context.snapshot, context.versions, context.positions, context.products
        )
        boundary, details = computed.boundary, computed.details
        proven = boundary.status != "INSUFFICIENT_EVIDENCE"
        ownership = details.current_goal_ownership
        goal_names = {
            row.id: row.name for row in session.scalars(select(Goal).where(Goal.user_id == user_id))
        }
        goal_items = [
            GoalOwnershipItem(
                **item.model_dump(exclude={"principal_position_ids"}),
                name=goal_names.get(item.goal_id, "已确认目标"),
            )
            for item in ownership.items
        ]
        position_rows = {
            row.id: row
            for row in session.scalars(
                select(AssetPosition).where(AssetPosition.user_id == user_id)
            )
        }
        exposure_proven = exposure is not None and not issues
        exposures = exposure or []
        managed_ids = {key for scope in exposures for key in scope.counted_position_ids}
        held = sum(
            position_rows[key].principal_cents
            for key in managed_ids
            if position_rows[key].status in {"HELD", "MATURED"}
        )
        redeeming = sum(
            position_rows[key].principal_cents
            for key in managed_ids
            if position_rows[key].status == "REDEEMING"
        )
        pending_items = [
            _action(session, context, row, audit.anchored_run_statuses[row.decision_run_id])
            for row in action_rows
        ]
        proposal_items = [
            _proposal(row, context, audit.anchored_run_statuses[row.id]) for row in proposal_rows
        ]
        pending_complete = pending_total <= pending_limit
        recovery_complete = recovery_total <= recovery_limit
        pending_proven = all(
            item.bank_state_proven and item.audit_status == "VALID" for item in pending_items
        )
        proposal_proven = all(item.status != "NOT_PROVEN" for item in proposal_items)
        pending = PendingActionsCard(
            state="INCOMPLETE"
            if not pending_complete
            else "PROVEN"
            if pending_proven
            else "NOT_PROVEN",
            total=pending_total,
            items=pending_items,
            list_complete=pending_complete,
            has_more=not pending_complete,
        )
        proposals = RecoveryProposalsCard(
            state="INCOMPLETE"
            if not recovery_complete
            else "PROVEN"
            if proposal_proven
            else "NOT_PROVEN",
            total=recovery_total,
            items=proposal_items,
            list_complete=recovery_complete,
            has_more=not recovery_complete,
        )
        return DashboardResponse(
            user_id=user_id,
            as_of=now,
            timezone=context.snapshot.timezone,
            account_facts=AccountFactsCard(
                state="PROVEN" if bank_matched and facts.accounts else "NOT_PROVEN",
                facts=facts,
                bank_projection_state="MATCHED" if bank_matched else "NOT_PROVEN",
                issues=issues if not bank_matched else [],
            ),
            boundary=financial_card(boundary, details, issues, digest),
            goal_ownership=GoalOwnershipCard(
                state="PROVEN" if ownership.status == "PROVEN" else "NOT_PROVEN",
                cash_owned_cents=ownership.cash_owned_cents,
                principal_owned_cents=ownership.principal_owned_cents,
                allocated_cents=ownership.allocated_cents,
                unassigned_goal_cash_cents=ownership.unassigned_goal_cash_cents,
                items=goal_items,
                issues=issues,
            ),
            managed_assets=ManagedAssetsCard(
                state="PROVEN" if exposure_proven else "NOT_PROVEN",
                managed_current_principal_cents=sum(s.managed_principal_cents for s in exposures)
                if exposure_proven
                else None,
                general_principal_cents=sum(
                    s.managed_principal_cents for s in exposures if s.scope == "general_idle_funds"
                )
                if exposure_proven
                else None,
                held_or_matured_cents=held if exposure_proven else None,
                redeeming_cents=redeeming if exposure_proven else None,
                pending_purchase_cents=sum(s.pending_purchase_cents for s in exposures)
                if exposure_proven
                else None,
                by_goal=[
                    ManagedGoalAmount(
                        goal_id=s.goal_id,
                        principal_cents=s.managed_principal_cents,
                        pending_purchase_cents=s.pending_purchase_cents,
                    )
                    for s in exposures
                    if s.goal_id is not None
                ]
                if exposure_proven
                else [],
                excluded_manual_count=len(
                    {key for s in exposures for key in s.excluded_manual_position_ids}
                ),
                unknown_position_count=sum(
                    row.status == "UNKNOWN" for row in position_rows.values()
                ),
                evidence_ids=sorted({key for s in exposures for key in s.evidence_ids}),
                issues=issues,
            ),
            next_obligations=details.next_obligations,
            pending_actions=pending,
            recovery_proposals=proposals,
            intervention=intervention(
                pending, proposals, sources_proven=proven, audit_complete=audit.complete
            ),
            audit=audit,
            source_evidence_ids=sorted(context.sources.used),
        )
