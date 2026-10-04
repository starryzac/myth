"""Proof completeness and isolated per-action bookkeeping without a database."""

from datetime import timedelta
from uuid import UUID

import pytest
from app.domain.autonomy_types import AutonomyDecision
from app.domain.boundary import compute_boundary
from app.services.boundary import BoundaryContext, Sources, clone_boundary_context
from app.services.dashboard import intervention
from app.services.dashboard_types import (
    PendingActionItem,
    PendingActionsCard,
    RecoveryProposalItem,
    RecoveryProposalsCard,
)
from app.tests.boundary_display_cases import NOW, snapshot


def empty_pending(*, complete: bool = True) -> PendingActionsCard:
    return PendingActionsCard(
        state="PROVEN" if complete else "INCOMPLETE",
        total=0 if complete else 1,
        items=[],
        list_complete=complete,
        has_more=not complete,
    )


def empty_proposals() -> RecoveryProposalsCard:
    return RecoveryProposalsCard(
        state="PROVEN", total=0, items=[], list_complete=True, has_more=False
    )


@pytest.mark.parametrize(
    "sources,audit,complete,expected",
    [
        (True, True, True, "NONE"),
        (False, True, True, "NOT_PROVEN"),
        (True, False, True, "NOT_PROVEN"),
        (True, True, False, "NOT_PROVEN"),
    ],
)
def test_empty_page_is_not_proof_of_no_intervention(
    sources: bool,
    audit: bool,
    complete: bool,
    expected: str,
) -> None:
    result = intervention(
        empty_pending(complete=complete),
        empty_proposals(),
        sources_proven=sources,
        audit_complete=audit,
    )
    assert result.status == expected
    assert result.complete == (sources and audit and complete)


def test_confirmed_exact_ask_does_not_ask_twice_and_unknown_outranks_new_confirmation() -> None:
    decision = AutonomyDecision(
        algorithm_version="autonomy-v1",
        level="ASK_ONCE",
        execution_eligible=True,
        financial_evaluation="VERIFIED",
        reasons=["EXPLICIT_TRANSFER"],
        confirmation_required=True,
        confirmation_satisfied=True,
    )
    item = PendingActionItem(
        action_id=UUID(int=1),
        decision_run_id=UUID(int=2),
        action_type="TRANSFER_INTERNAL",
        amount_cents=100,
        status="AUTHORIZED",
        prepared_level="ASK_ONCE",
        prepared_at=NOW,
        current_decision=decision,
        effect_hash="0" * 64,
        fee_cents=0,
        loss_cents=0,
        bank_operation_id=None,
        bank_status=None,
        bank_state_proven=True,
        receipt_id=None,
        receipt_status=None,
        receipt_verified=False,
        audit_status="VALID",
        reason_codes=[],
    )
    pending = empty_pending().model_copy(update={"total": 1, "items": [item]})
    assert (
        intervention(pending, empty_proposals(), sources_proven=True, audit_complete=True).status
        == "NONE"
    )
    asking = item.model_copy(
        update={
            "current_decision": decision.model_copy(
                update={"confirmation_satisfied": False, "execution_eligible": False}
            )
        }
    )
    pending = pending.model_copy(update={"items": [asking]})
    assert (
        intervention(pending, empty_proposals(), sources_proven=True, audit_complete=True).status
        == "CONFIRMATION_REQUIRED"
    )
    unknown = item.model_copy(update={"status": "UNKNOWN", "current_decision": None})
    result = intervention(
        pending.model_copy(update={"total": 2, "items": [asking, unknown]}),
        empty_proposals(),
        sources_proven=False,
        audit_complete=True,
    )
    assert result.status == "RECONCILIATION_REQUIRED"
    assert result.known_required_count == 1  # Two views of one immutable operation, one count.
    assert result.complete is False


def test_legacy_recovery_proposal_is_review_without_an_action_identity_or_new_grant() -> None:
    proposal = RecoveryProposalItem(
        run_id=UUID(int=9),
        as_of=NOW,
        status="REVIEW_REQUIRED",
        original_status="ASK_ONCE",
        fee_cents=100,
        loss_cents=1_000,
        audit_status="VALID",
        reason_codes=["ORIGINAL_PROPOSAL"],
    )
    proposals = empty_proposals().model_copy(update={"total": 1, "items": [proposal]})
    result = intervention(empty_pending(), proposals, sources_proven=True, audit_complete=True)
    assert result.status == "REVIEW_REQUIRED"
    assert result.known_required_count == 1
    assert "action_id" not in proposal.model_dump()


def test_read_context_clone_shares_financial_facts_but_never_leaks_action_issues() -> None:
    snap = snapshot()
    sources = Sources(UUID(int=1), NOW, [])
    sources.used.add(UUID(int=20))
    context = BoundaryContext(snap, [], [], [], sources)
    before = compute_boundary(snap, [], [], []).model_dump(mode="json")
    copied = clone_boundary_context(context, UUID(int=1), NOW)
    copied.sources.issue("ACTION_ONLY", "self", "one action's validation")
    copied.sources.used.add(UUID(int=21))
    assert context.sources.issues == []
    assert context.sources.used == {UUID(int=20)}
    assert copied.snapshot is context.snapshot
    assert compute_boundary(context.snapshot, [], [], []).model_dump(mode="json") == before
    with pytest.raises(ValueError, match="tenant"):
        clone_boundary_context(context, UUID(int=2), NOW)
    with pytest.raises(ValueError, match="clock"):
        clone_boundary_context(context, UUID(int=1), NOW + timedelta(seconds=1))
