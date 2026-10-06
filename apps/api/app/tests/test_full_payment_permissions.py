"""SYNTHETIC_ROLE and original-shape pure risks, not real banking or human evidence."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

import pytest
from app.domain.execution_types import CashUse, ExecutionEffect, OccurrenceReference
from app.domain.full_payment_permissions import (
    PaymentConfirmRequest,
    PaymentPrepareRequest,
    PaymentRelationScope,
    PaymentStartRequest,
    account_payment_identity,
    original_payment_action_key,
    payment_command_identity,
    payment_scope_hash,
    require_payment_principal,
    validate_payment_bridge,
    verify_original_payment_references,
    verify_payment_effect,
)
from app.domain.full_policy_configuration import PeriodicTransferPolicy
from app.domain.local_actor_session_types import ActorRole, LocalActorPrincipal
from app.domain.policy_configuration import ExactAmount, RecurringObligation, configuration_hash
from app.services.full_payment_permissions import PaymentCommandOriginal, PaymentUserActionConsent
from pydantic import ValidationError

USER, EPOCH, ACCOUNT, POLICY, VERSION, FULL, FULL_VERSION, PROOF, ACTION = (
    UUID(int=n) for n in range(9600, 9609)
)
NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


def principal(role: ActorRole = "USER") -> LocalActorPrincipal:
    return LocalActorPrincipal(
        user_id=USER,
        role=role,
        session_id=UUID(int=9620),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )


def scope() -> PaymentRelationScope:
    return PaymentRelationScope(
        user_id=USER,
        epoch_id=EPOCH,
        full_policy_id=FULL,
        full_version_id=FULL_VERSION,
        full_configuration_hash="1" * 64,
        original_policy_id=POLICY,
        original_version_id=VERSION,
        original_configuration_hash="2" * 64,
        payee_id="known-synthetic-payee",
        payee_evidence_id=PROOF,
        payee_evidence_hash="3" * 64,
        source_account_id=ACCOUNT,
        source_account_identity_hash="4" * 64,
        amount_rule=ExactAmount(kind="exact", amount_cents=180000),
        due_day=5,
        single_action_cap_cents=180000,
        auto_execute=True,
        timezone="UTC",
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=60),
    )


def effect() -> ExecutionEffect:
    return ExecutionEffect(
        operation_id=ACTION,
        user_id=USER,
        business_key=f"recurring:{POLICY}:2026-10",
        action_type="PAY_RECURRING",
        amount_cents=170000,
        cash_uses=[CashUse(account_id=ACCOUNT, amount_cents=170000)],
        policy_id=POLICY,
        policy_version_id=VERSION,
        policy_version_ids=[VERSION],
        liability=OccurrenceReference(policy_id=POLICY, period="2026-10", evidence_ids=[PROOF]),
        payee_id="known-synthetic-payee",
        payee_evidence_id=PROOF,
        valid_from=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


def start_body() -> dict[str, Any]:
    return {
        "expected_epoch_id": str(EPOCH),
        "full_policy_id": str(FULL),
        "expected_full_version_id": str(FULL_VERSION),
        "original_policy_id": str(POLICY),
        "expected_original_version_id": str(VERSION),
        "idempotency_key": "original-start",
    }


def original() -> PaymentCommandOriginal:
    body = start_body()
    return PaymentCommandOriginal(
        command_id=payment_command_identity(USER, EPOCH, body["idempotency_key"]),
        user_id=USER,
        epoch_id=EPOCH,
        kind="START",
        idempotency_key=body["idempotency_key"],
        start_command_id=None,
        original_request=body,
        request_hash=configuration_hash({"user_id": str(USER), "kind": "START", "request": body}),
        principal_at_command=principal(),
        scope=scope(),
        scope_hash=payment_scope_hash(scope()),
        recorded_at=NOW,
    )


def reference_fixture() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    account = {
        "id": str(ACCOUNT),
        "user_id": str(USER),
        "account_type": "CASH",
        "currency": "CNY",
        "external_ref": "source-001",
        "bank_code": "ICBC",
        "balance_cents": 900000,
    }
    content = {"economic_role": "CONSUMPTION", "counterparty_ref": "known-synthetic-payee"}
    payee = {
        "id": str(PROOF),
        "user_id": str(USER),
        "status": "VALID",
        "evidence_level": "BANK_CONFIRMED",
        "content": content,
        "content_hash": configuration_hash(content),
    }
    references = [
        {
            "role": "source_account",
            "kind": "ACCOUNT",
            "id": str(ACCOUNT),
            "binding_hash": configuration_hash(
                {"id": str(ACCOUNT), "owner": str(USER), "type": "CASH"}
            ),
            "snapshot": deepcopy(account),
        },
        {
            "role": "payee_source",
            "kind": "EVIDENCE",
            "id": str(PROOF),
            "binding_hash": configuration_hash(
                {
                    "id": str(PROOF),
                    "hash": payee["content_hash"],
                    "payee_id": "known-synthetic-payee",
                }
            ),
            "snapshot": deepcopy(payee),
        },
    ]
    return references, account, payee


def test_signed_user_is_required_for_initiation_not_just_a_post_or_role_label() -> None:
    require_payment_principal(principal(), USER, NOW, {"USER"})
    for actor in (
        None,
        principal("AGENT"),
        principal("SYSTEM"),
        principal("REVIEWER"),
        principal().model_copy(update={"user_id": UUID(int=1)}),
        principal().model_copy(update={"expires_at": NOW}),
    ):
        with pytest.raises(ValueError):
            require_payment_principal(actor, USER, NOW, {"USER"})
    assert principal().human_identity_verified is False


@pytest.mark.parametrize(
    "field", ["actor", "initiator", "amount_cents", "result", "bank_status", "now"]
)
def test_client_cannot_supply_financial_inputs_or_server_roles(field: str) -> None:
    with pytest.raises(ValidationError):
        PaymentStartRequest.model_validate_json(json.dumps(start_body() | {field: True}))


@pytest.mark.parametrize("acceptance", [False, 1, "true"])
def test_exact_acceptance_cannot_be_coerced(acceptance: Any) -> None:
    with pytest.raises(ValidationError):
        PaymentConfirmRequest.model_validate_json(
            json.dumps(
                {
                    "expected_epoch_id": str(EPOCH),
                    "reviewed_scope_hash": "a" * 64,
                    "accepted": acceptance,
                    "reason": "explicit",
                    "idempotency_key": "original-confirm",
                }
            )
        )


def test_original_user_and_exact_start_body_remain_bound_when_hash_is_recomputed() -> None:
    saved = original()
    assert PaymentCommandOriginal.model_validate_json(saved.model_dump_json()) == saved
    changed = saved.model_dump(mode="json")
    changed["original_request"]["full_policy_id"] = str(UUID(int=1))
    changed["request_hash"] = configuration_hash(
        {"user_id": str(USER), "kind": "START", "request": changed["original_request"]}
    )
    with pytest.raises(ValidationError):
        PaymentCommandOriginal.model_validate_json(json.dumps(changed))
    changed = saved.model_dump(mode="json")
    changed["principal_at_command"]["role"] = "AGENT"
    with pytest.raises(ValidationError):
        PaymentCommandOriginal.model_validate_json(json.dumps(changed))


def test_same_key_never_changes_original_command_and_prepare_identity() -> None:
    assert payment_command_identity(USER, EPOCH, "k") != payment_command_identity(
        USER, UUID(int=1), "k"
    )
    assert original_payment_action_key(ACTION, "k") != original_payment_action_key(ACTION, "other")
    with pytest.raises(ValidationError):
        PaymentPrepareRequest(expected_epoch_id=EPOCH, period="2026-13", idempotency_key="k")


def test_real_unpaid_partial_amount_is_accepted_but_policy_amount_is_not_invented() -> None:
    verify_payment_effect(scope(), effect(), NOW)
    assert scope().creates_original_mvp_permission is scope().full_planning_bank_authority is False


@pytest.mark.parametrize(
    "change",
    [
        {"policy_version_id": UUID(int=1)},
        {"policy_version_ids": [VERSION, UUID(int=1)]},
        {"payee_id": "new-unconfirmed"},
        {"payee_evidence_id": UUID(int=1)},
        {"cash_uses": [CashUse(account_id=UUID(int=1), amount_cents=170000)]},
        {"amount_cents": 180001},
        {"business_key": "different-month"},
    ],
)
def test_effect_scope_cannot_be_rehashed_to_another_version_payee_account_or_cap(
    change: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        verify_payment_effect(scope(), effect().model_copy(update=change), NOW)


def test_due_day_clamps_february_and_scope_expiry_never_authorizes_future_occurrence() -> None:
    at = datetime(2028, 2, 29, 12, tzinfo=UTC)
    bound = scope().model_copy(
        update={
            "due_day": 31,
            "valid_from": at - timedelta(days=2),
            "valid_until": at + timedelta(days=2),
        }
    )
    action = effect().model_copy(
        update={
            "valid_from": at,
            "expires_at": at + timedelta(minutes=5),
            "liability": OccurrenceReference(
                policy_id=POLICY, period="2028-02", evidence_ids=[PROOF]
            ),
            "business_key": f"recurring:{POLICY}:2028-02",
        }
    )
    verify_payment_effect(bound, action, at)
    with pytest.raises(ValueError):
        verify_payment_effect(bound, action, at - timedelta(days=1))
    with pytest.raises(ValueError):
        verify_payment_effect(bound, action, bound.valid_until)


def test_only_new_verified_same_payee_proof_may_replace_effects_evidence_id() -> None:
    newer = UUID(int=9690)
    action = effect().model_copy(update={"payee_evidence_id": newer})
    verify_payment_effect(scope(), action, NOW, verified_payee_evidence_ids={newer})
    with pytest.raises(ValueError):
        verify_payment_effect(scope(), action, NOW)
    with pytest.raises(ValueError):
        verify_payment_effect(
            scope(),
            action.model_copy(update={"payee_id": "different"}),
            NOW,
            verified_payee_evidence_ids={newer},
        )


def test_original_full_references_do_not_swap_to_a_new_same_payee_transaction() -> None:
    refs, account, bank = reference_fixture()
    verify_original_payment_references(refs, account, bank, "known-synthetic-payee")
    account["balance_cents"] -= 170000  # cash movement is verified separately against real bank.
    verify_original_payment_references(refs, account, bank, "known-synthetic-payee")
    assert account_payment_identity(account) == account_payment_identity(refs[0]["snapshot"])
    newer = deepcopy(bank)
    newer["id"] = str(UUID(int=9690))
    with pytest.raises(ValueError):
        verify_original_payment_references(refs, account, newer, "known-synthetic-payee")


@pytest.mark.parametrize(
    "which,field,value",
    [
        ("account", "external_ref", "other-bank-account"),
        ("account", "currency", "USD"),
        ("account", "user_id", str(UUID(int=1))),
        ("bank", "status", "SUPERSEDED"),
        ("bank", "evidence_level", "USER_DECLARED"),
        ("bank", "content_hash", "f" * 64),
    ],
)
def test_original_identity_mutations_and_missing_reference_denominators_are_rejected(
    which: str,
    field: str,
    value: Any,
) -> None:
    refs, account, bank = reference_fixture()
    (account if which == "account" else bank)[field] = value
    with pytest.raises(ValueError):
        verify_original_payment_references(refs, account, bank, "known-synthetic-payee")
    with pytest.raises(ValueError):
        verify_original_payment_references(refs[:1], account, bank, "known-synthetic-payee")


def test_full_mapping_cannot_increase_the_old_payee_or_monthly_permission() -> None:
    common = {
        "payee_id": "known",
        "amount_rule": {"kind": "exact", "amount_cents": 100},
        "due_day": 5,
        "auto_execute": True,
    }
    full = PeriodicTransferPolicy.model_validate(
        common
        | {
            "type": "periodic_transfer",
            "source_account_id": ACCOUNT,
            "single_action_cap_cents": 100,
        }
    )
    old = RecurringObligation.model_validate(common | {"type": "recurring_obligation"})
    validate_payment_bridge(full, old)
    for changed in ({"auto_execute": False}, {"payee_id": "new"}, {"due_day": 6}):
        with pytest.raises(ValueError):
            validate_payment_bridge(full.model_copy(update=changed), old)


def test_each_ask_consent_is_an_original_signed_user_and_exact_old_confirmation() -> None:
    consent = PaymentUserActionConsent(
        user_id=USER,
        epoch_id=EPOCH,
        action_id=ACTION,
        original_effect_hash="a" * 64,
        original_confirmation_evidence_id=uuid5(ACTION, "confirmation:" + "a" * 64),
        principal_at_confirmation=principal(),
        confirmed_at=NOW,
    )
    for changed in (
        {"principal_at_confirmation": principal("AGENT").model_dump(mode="json")},
        {"original_confirmation_evidence_id": str(UUID(int=1))},
    ):
        with pytest.raises(ValidationError):
            PaymentUserActionConsent.model_validate_json(
                json.dumps(consent.model_dump(mode="json") | changed)
            )
