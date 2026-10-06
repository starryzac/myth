"""Pure protocol/delegation risks only; no real bank or authorization evidence."""

from copy import deepcopy
from typing import Any, cast
from uuid import UUID, uuid5

import pytest
from app.db.models import ActionPlan, ActionReceipt, BankOperation, EvidenceItem
from app.domain.policy_configuration import configuration_hash
from app.services import full_goal_release_execution as service
from app.services.full_goal_release_execution import (
    GoalReleaseExecuteRequest,
    GoalReleasePrepareRequest,
    release_action_identity,
    release_bank_key,
    select_original_release_uses,
    validate_goal_release_exposure,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_goal_release_execution import basis, command
from app.tests.test_goal_release_provenance import NOW
from pydantic import ValidationError
from sqlalchemy.orm import Session


def body() -> dict[str, Any]:
    effect = command().effect
    return {
        "policy_id": str(effect.policy_id),
        "source_goal_id": str(effect.source_goal_id),
        "expected_policy_version_id": str(effect.policy_version_id),
        "expected_goal_policy_version_id": str(effect.original_goal_policy_version_id),
        "expected_epoch_id": str(effect.epoch_id),
        "authorization_epoch_id": str(effect.epoch_id),
        "authorization_idempotency_key": "synthetic",
        "destination_account_id": str(effect.destination_account_id),
        "idempotency_key": "synthetic",
    }


def action(status: str = "PLANNED") -> ActionPlan:
    original = command()
    effect = original.effect
    request = {
        "original_prepare_request": body(),
        "goal_release_execution": original.model_dump(mode="json"),
        "goal_release_source_basis": basis().model_dump(mode="json"),
    }
    return ActionPlan(
        id=effect.operation_id,
        user_id=effect.user_id,
        created_at=NOW,
        decision_run_id=UUID(int=9910),
        policy_version_id=effect.original_goal_policy_version_id,
        source_account_id=effect.source_account_id,
        destination_account_id=effect.destination_account_id,
        goal_id=effect.source_goal_id,
        product_id=None,
        position_id=None,
        action_type="RELEASE_GOAL",
        amount_cents=effect.amount_cents,
        autonomy_level="ASK_ONCE",
        status=status,
        idempotency_key=effect.bank_idempotency_key,
        request=request,
        request_hash=configuration_hash(request),
        authorized_at=None,
        expires_at=effect.expires_at,
    )


@pytest.mark.parametrize(
    "field",
    [
        "amount_cents",
        "now",
        "user_id",
        "accepted",
        "grant",
        "result",
        "bank_facts",
        "source_basis",
        "emergency",
        "policy_configuration",
    ],
)
def test_public_prepare_never_accepts_financial_or_permission_input(field: str) -> None:
    with pytest.raises(ValidationError):
        GoalReleasePrepareRequest.model_validate_json(
            __import__("json").dumps(body() | {field: True})
        )


def consent() -> dict[str, Any]:
    original = command()
    return {
        "accepted": True,
        "reviewed_effect_hash": original.effect_hash,
        "expected_epoch_id": str(original.effect.epoch_id),
    }


@pytest.mark.parametrize("field", ["effect", "amount_cents", "bank_status", "now", "grant"])
def test_execute_body_only_accepts_explicit_original_effect_consent(field: str) -> None:
    with pytest.raises(ValidationError):
        GoalReleaseExecuteRequest.model_validate_json(
            __import__("json").dumps(consent() | {field: True})
        )


@pytest.mark.parametrize("accepted", [False, 1, "true", None])
def test_execute_requires_strict_true_not_coercion(accepted: Any) -> None:
    with pytest.raises(ValidationError):
        GoalReleaseExecuteRequest.model_validate_json(
            __import__("json").dumps(consent() | {"accepted": accepted})
        )


def test_missing_consent_and_mismatched_original_effect_or_epoch_never_execute() -> None:
    with pytest.raises(ValidationError):
        GoalReleaseExecuteRequest.model_validate({})
    original = command()
    valid = GoalReleaseExecuteRequest.model_validate_json(__import__("json").dumps(consent()))
    service.validate_goal_release_execute_request(valid, original)
    for change in ({"reviewed_effect_hash": "0" * 64}, {"expected_epoch_id": str(UUID(int=1))}):
        wrong = GoalReleaseExecuteRequest.model_validate_json(
            __import__("json").dumps(consent() | change)
        )
        with pytest.raises(PolicyLifecycleError, match="逐行动确认"):
            service.validate_goal_release_execute_request(wrong, original)


def original_confirmation() -> EvidenceItem:
    original = command()
    effect = original.effect
    content = {
        "protocol": "goal-release-action-confirmation-v1",
        "simulation": True,
        "user_id": str(effect.user_id),
        "action_id": str(effect.operation_id),
        "epoch_id": str(effect.epoch_id),
        "effect_hash": original.effect_hash,
        "accepted": True,
        "confirmed_at": NOW.isoformat(),
        "valid_until": effect.expires_at.isoformat(),
    }
    return EvidenceItem(
        id=uuid5(effect.operation_id, "release-confirmation:" + original.effect_hash),
        user_id=effect.user_id,
        created_at=NOW,
        evidence_level="USER_CONFIRMED_ACTION",
        source_type="USER_GOAL_RELEASE_ACTION_CONFIRMATION",
        source_ref=str(effect.operation_id),
        content=content,
        content_hash=configuration_hash(content),
        status="VALID",
        observed_at=NOW,
        valid_from=NOW,
        valid_to=effect.expires_at,
    )


def test_original_action_consent_is_bound_not_an_unchecked_success_label() -> None:
    class OriginalOnly:
        def __init__(self, proof: EvidenceItem | None) -> None:
            self.proof = proof

        def get(self, *_args: Any) -> EvidenceItem | None:
            return self.proof

    proof = original_confirmation()
    assert (
        service.read_goal_release_action_confirmation(cast(Session, OriginalOnly(None)), command())
        is None
    )
    assert (
        service.read_goal_release_action_confirmation(cast(Session, OriginalOnly(proof)), command())
        is proof
    )
    for field, changed in (
        ("id", UUID(int=1)),
        ("user_id", UUID(int=1)),
        ("evidence_level", "BANK_CONFIRMED"),
        ("source_ref", "other"),
        ("content_hash", "0" * 64),
        ("status", "INVALID"),
        ("valid_to", NOW),
    ):
        bad = original_confirmation()
        setattr(bad, field, changed)
        with pytest.raises(PolicyLifecycleError, match="逐行动确认"):
            service.read_goal_release_action_confirmation(
                cast(Session, OriginalOnly(bad)), command()
            )
    for changed_content in ({"accepted": 1}, {"simulation": 1}, {"effect_hash": "0" * 64}):
        bad = original_confirmation()
        bad.content = bad.content | changed_content
        bad.content_hash = configuration_hash(bad.content)
        with pytest.raises(PolicyLifecycleError, match="逐行动确认"):
            service.read_goal_release_action_confirmation(
                cast(Session, OriginalOnly(bad)), command()
            )


def test_source_selection_is_exact_canonical_and_does_not_mutate_original_assigned_slices() -> None:
    first = command(70).effect.release_uses[0]
    second = first.model_copy(update={"allocation_action_id": UUID(int=1), "amount_cents": 80})
    sources = [first, second]
    before = [row.model_dump_json() for row in sources]
    selected = select_original_release_uses(sources, 100)
    assert [row.allocation_action_id for row in selected] == [
        UUID(int=1),
        first.allocation_action_id,
    ]
    assert [row.amount_cents for row in selected] == [80, 20]
    assert [row.model_dump_json() for row in sources] == before
    with pytest.raises(ValueError):
        select_original_release_uses(sources, 151)
    with pytest.raises(ValueError):
        select_original_release_uses([first, first], 1)


@pytest.mark.parametrize("amount", [True, 0, -1, 1.0])
def test_source_selector_does_not_coerce_amount(amount: Any) -> None:
    with pytest.raises(ValueError):
        select_original_release_uses(command().effect.release_uses, amount)


def test_original_key_binds_actual_user_epoch_and_does_not_become_final_absence() -> None:
    effect = command().effect
    one = release_action_identity(effect.user_id, effect.epoch_id, "key")
    assert one == release_action_identity(effect.user_id, effect.epoch_id, "key")
    assert one != release_action_identity(UUID(int=1), effect.epoch_id, "key")
    assert one != release_action_identity(effect.user_id, UUID(int=1), "key")
    assert release_bank_key(effect.user_id, effect.epoch_id, "key") != release_bank_key(
        effect.user_id, effect.epoch_id, "other"
    )
    unknown = service.GoalReleaseLookup(
        user_id=effect.user_id,
        epoch_id=effect.epoch_id,
        idempotency_key="key",
        status="NOT_FOUND_NOT_FINAL",
        original=None,
    )
    assert not unknown.not_found_is_final and not unknown.replacement_allowed


def test_pending_release_is_explicitly_unreserved_not_a_fictitious_income_claim() -> None:
    original = action("SUBMITTED")
    before = deepcopy(original.request)
    validated = validate_goal_release_exposure(
        original,
        {"action_id": str(original.id), "state": "RELEASE_PENDING_UNRESERVED"},
        [],
        [],
        [],
        [],
        NOW,
    )
    assert validated.effect.amount_cents == 100 and original.request == before
    with pytest.raises(ValueError):
        validate_goal_release_exposure(
            original,
            {"action_id": str(original.id), "state": "EXECUTION_RESERVED"},
            [],
            [],
            [],
            [],
            NOW,
        )


@pytest.mark.parametrize(
    "state,status",
    [
        ("PLANNED_UNRESERVED", "SUBMITTED"),
        ("RELEASE_PENDING_UNRESERVED", "UNKNOWN"),
        ("NO_EFFECT", "UNKNOWN"),
        ("EXECUTION_SETTLED", "SUCCEEDED"),
    ],
)
def test_state_names_never_hide_unknown_or_replace_receipt(state: str, status: str) -> None:
    original = action(status)
    with pytest.raises(ValueError):
        validate_goal_release_exposure(
            original, {"action_id": str(original.id), "state": state}, [], [], [], [], NOW
        )


def test_settled_exposure_requires_actual_reader_with_exact_original_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = action("SUCCEEDED")
    bank = BankOperation(id=original.id, action_plan_id=original.id, user_id=original.user_id)
    receipt = ActionReceipt(id=UUID(int=9950), action_plan_id=original.id, user_id=original.user_id)
    declaration = {
        "action_id": str(original.id),
        "state": "EXECUTION_SETTLED",
        "receipt_id": str(receipt.id),
    }
    with pytest.raises(ValueError):
        validate_goal_release_exposure(original, declaration, [receipt], [bank], [], [], NOW)
    calls = []
    monkeypatch.setattr(service, "verify_goal_release_receipt", lambda *args: calls.append(args))
    reader = cast(Session, object())
    validate_goal_release_exposure(
        original, declaration, [receipt], [bank], [], [], NOW, session=reader
    )
    assert calls == [(reader, bank, receipt, NOW)]  # Delegation only, never economic proof.
