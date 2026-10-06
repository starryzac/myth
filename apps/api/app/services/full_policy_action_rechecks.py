"""Recheck known FULL action adapters in the same policy-command/user-lock transaction."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    BankOperation,
    SimulatedBankPosting,
)
from app.domain.boundary_types import BoundaryModel
from app.domain.full_policy_action_rechecks import release_recheck_disposition
from app.services.audit_recording import audit_subject_data, record_action_transition
from app.services.decision_trace import get_decision_trace
from app.services.execution_exposure import refresh_execution_exposure
from app.services.full_goal_release_reader import (
    read_goal_release_command,
    verify_goal_release_receipt,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _user
from pydantic import Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session


class FullActionRecheckResult(BoundaryModel):
    full_policy_id: UUID
    known_adapter: Literal["GOAL_RELEASE"] = "GOAL_RELEASE"
    invalidated_action_ids: list[UUID] = Field(default_factory=list)
    inflight_action_ids: list[UUID] = Field(default_factory=list)
    terminal_action_ids: list[UUID] = Field(default_factory=list)
    retained_action_ids: list[UUID] = Field(default_factory=list)
    funds_withdrawn: Literal[False] = False


def recheck_full_goal_release_actions(
    session: Session,
    user_id: UUID,
    full_policy_id: UUID,
    current_version_id: UUID,
    current_status: str,
    command_id: UUID,
    now: datetime,
) -> FullActionRecheckResult:
    """Call only after a new command, before committing it; original replays skip it.

    Only the dedicated goal-release adapter is covered. This does not claim to
    invalidate other FULL action families. The existing bank acceptance adapter
    independently checks current authority under the same original User lock.
    """
    _user(session, user_id)
    result = FullActionRecheckResult(full_policy_id=full_policy_id)
    actions = list(
        session.scalars(
            select(ActionPlan)
            .where(ActionPlan.user_id == user_id, ActionPlan.action_type == "RELEASE_GOAL")
            .order_by(ActionPlan.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    declarations: dict[UUID, dict[str, str]] = {}
    for action in actions:
        command = read_goal_release_command(action)
        if command.effect.policy_id != full_policy_id:
            continue
        trace = get_decision_trace(session, user_id, action.decision_run_id, now)
        if trace.completeness != "COMPLETE" or trace.audit_chain_status != "VALID":
            raise PolicyLifecycleError(
                "FULL_ACTION_RECHECK_UNVERIFIED", "原回拨决策或审计未验真，不能声明已重查", 409
            )
        banks = list(
            session.scalars(
                select(BankOperation).where(
                    or_(BankOperation.action_plan_id == action.id, BankOperation.id == action.id)
                )
            )
        )
        receipts = list(
            session.scalars(select(ActionReceipt).where(ActionReceipt.action_plan_id == action.id))
        )
        # The new dedicated protocol requires operation.id == original action.id.
        # Include owner rows and malformed rows targeting any observed operation.
        bank_ids = {action.id, *(bank.id for bank in banks)}
        postings = list(
            session.scalars(
                select(SimulatedBankPosting).where(SimulatedBankPosting.operation_id.in_(bank_ids))
            )
        )
        settled = False
        claims = list(
            session.scalars(
                select(ActionResourceReservation).where(
                    ActionResourceReservation.action_plan_id == action.id
                )
            )
        )
        if claims:
            # Dedicated release never reserves income or principal. An unexpected
            # row must not be discarded merely because its bank result is absent.
            result.inflight_action_ids.append(action.id)
            continue
        if (
            action.status in {"SUCCEEDED", "RECONCILED"}
            and len(banks) == len(receipts) == 1
            and banks[0].status == "SETTLED"
        ):
            verify_goal_release_receipt(session, banks[0], receipts[0], now)
            settled = True
        disposition = release_recheck_disposition(
            action.status,
            permission_changed=current_status not in {"ACTIVE", "CONFIRMED"}
            or command.effect.policy_version_id != current_version_id,
            bank_operation_count=len(banks),
            receipt_count=len(receipts),
            posting_count=len(postings),
            verified_final_settlement=settled,
        )
        if disposition == "INVALIDATE_UNSUBMITTED":
            before_status, before_data = action.status, audit_subject_data(action)
            action.status = "INVALIDATED"
            session.flush()
            record_action_transition(
                session,
                action,
                before_status,
                now,
                reason_code="FULL_POLICY_PERMISSION_CHANGED_BEFORE_BANK_ACCEPT",
                cause_ref=f"full-policy-command:{command_id}",
                details={
                    "full_policy_id": str(full_policy_id),
                    "current_full_policy_version_id": str(current_version_id),
                    "original_full_policy_version_id": str(command.effect.policy_version_id),
                    "no_effect_status": "CONFIRMED",
                    "original_bank_operation_count": 0,
                    "original_receipt_count": 0,
                    "original_posting_count": 0,
                    "creates_new_income": False,
                },
                before_data=before_data,
            )
            declarations[action.id] = {"action_id": str(action.id), "state": "NO_EFFECT"}
            result.invalidated_action_ids.append(action.id)
        elif disposition == "RETAIN_INFLIGHT":
            result.inflight_action_ids.append(action.id)
        elif disposition == "TERMINAL":
            result.terminal_action_ids.append(action.id)
        else:
            result.retained_action_ids.append(action.id)
    if declarations:
        refresh_execution_exposure(session, user_id, now, command_id, declarations=declarations)
    return result
