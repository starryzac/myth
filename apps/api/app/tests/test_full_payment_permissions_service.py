"""Service guard doubles only: no produced bank facts or successful financial results."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models import Account, DecisionRun, EvidenceItem, Policy, PolicyVersion, User
from app.domain.full_payment_permissions import PaymentExecuteRequest, PaymentScopeRequest
from app.domain.full_policy_configuration import PeriodicTransferPolicy
from app.domain.policy_configuration import RecurringObligation, configuration_hash
from app.services import full_payment_permissions as service
from app.services.audit_chain import row_copy
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_payment_permissions import (
    ACCOUNT,
    ACTION,
    EPOCH,
    FULL,
    FULL_VERSION,
    NOW,
    POLICY,
    PROOF,
    USER,
    VERSION,
    effect,
    original,
    principal,
    start_body,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def test_missing_mandatory_bank_hook_never_invokes_old_execute_or_creates_bank_operation() -> None:
    with pytest.raises(PolicyLifecycleError) as failed:
        service.execute_full_payment(
            cast(Engine, None),
            USER,
            ACTION,
            PaymentExecuteRequest(expected_epoch_id=EPOCH),
            principal(),
            NOW,
        )
    assert failed.value.code == "PAYMENT_BANK_GUARD_NOT_INSTALLED"
    assert failed.value.status_code == 503


def test_all_current_same_payee_bank_sources_are_validated_not_only_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newer = UUID(int=9701)
    rows = [SimpleNamespace(evidence_id=PROOF), SimpleNamespace(evidence_id=newer)]
    fake = SimpleNamespace(scalars=lambda _query: rows)
    seen: list[UUID] = []

    def verify(_session: Any, user: UUID, payee: str, proof: UUID, _now: Any) -> UUID:
        assert user == USER and payee == "known-synthetic-payee"
        seen.append(proof)
        if proof == newer:
            raise PolicyLifecycleError(
                "EXECUTION_SOURCE_UNKNOWN", "actual bank identity conflict", 409
            )
        return proof

    monkeypatch.setattr(service, "_counterparty_binding", verify)
    with pytest.raises(PolicyLifecycleError):
        service._current_payee_sources(cast(Session, fake), USER, "known-synthetic-payee", NOW)
    assert seen == [PROOF, newer]
    rows[1].evidence_id = None
    seen.clear()
    with pytest.raises(PolicyLifecycleError):
        service._current_payee_sources(cast(Session, fake), USER, "known-synthetic-payee", NOW)
    assert seen == []


def test_new_verified_same_payee_proofs_preserve_all_original_proof_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newer = UUID(int=9701)
    fake = SimpleNamespace(
        scalars=lambda _query: [
            SimpleNamespace(evidence_id=PROOF),
            SimpleNamespace(evidence_id=newer),
        ]
    )
    monkeypatch.setattr(service, "_counterparty_binding", lambda _s, _u, _p, proof, _n: proof)
    assert service._current_payee_sources(
        cast(Session, fake), USER, "known-synthetic-payee", NOW
    ) == {
        PROOF,
        newer,
    }


def test_missing_original_binding_cannot_downgrade_a_recorded_full_action_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(model: Any, _id: Any) -> Any:
        return None if model is EvidenceItem else object() if model is DecisionRun else None

    @contextmanager
    def reader(_engine: Any) -> Any:
        yield SimpleNamespace(get=get)

    monkeypatch.setattr(service, "_reader", reader)
    with pytest.raises(PolicyLifecycleError) as failed:
        service.enforce_full_payment_bank_scope(cast(Engine, None), USER, effect(), NOW)
    assert failed.value.code == "PAYMENT_ORIGINAL_INTEGRITY_ERROR"


@pytest.mark.parametrize("mutate_original", [False, True])
def test_scope_narrow_latest_evidence_exception_keeps_original_full_flags_and_rejects_old_tamper(
    monkeypatch: pytest.MonkeyPatch,
    mutate_original: bool,
) -> None:
    """Actual _scope branch with service doubles, not real DB bank verification."""
    common = {
        "payee_id": "known-synthetic-payee",
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
            "valid_until": "2026-12-31",
        }
    )
    old = RecurringObligation.model_validate(common | {"type": "recurring_obligation"})
    source = Account(
        id=ACCOUNT,
        user_id=USER,
        account_type="CASH",
        currency="CNY",
        bank_code="ICBC",
        external_ref="source-001",
        balance_cents=900000,
        created_at=NOW,
        observed_at=NOW,
        name="risk",
    )
    content = {"economic_role": "CONSUMPTION", "counterparty_ref": "known-synthetic-payee"}
    proof = EvidenceItem(
        id=PROOF,
        user_id=USER,
        created_at=NOW,
        observed_at=NOW,
        valid_from=NOW,
        evidence_level="BANK_CONFIRMED",
        source_type="SYNTHETIC_TOOL_ONLY",
        source_ref="original",
        status="VALID",
        content=content,
        content_hash=configuration_hash(content),
    )
    refs = [
        {
            "role": "source_account",
            "kind": "ACCOUNT",
            "id": str(ACCOUNT),
            "binding_hash": configuration_hash(
                {"id": str(ACCOUNT), "owner": str(USER), "type": "CASH"}
            ),
            "snapshot": row_copy(source),
        },
        {
            "role": "payee_source",
            "kind": "EVIDENCE",
            "id": str(PROOF),
            "binding_hash": configuration_hash(
                {"id": str(PROOF), "hash": proof.content_hash, "payee_id": common["payee_id"]}
            ),
            "snapshot": row_copy(proof),
        },
    ]
    full_view = SimpleNamespace(
        template_name="PeriodicTransferPolicy",
        epoch_id=EPOCH,
        effective_status="ACTIVE",
        policy_id=FULL,
        planning_confirmation_valid=False,
        reference_validation="CHANGED_OR_UNAVAILABLE",
        current_version=SimpleNamespace(
            version_id=FULL_VERSION,
            configuration=full.model_dump(mode="json"),
            content_hash=configuration_hash(full.model_dump(mode="json")),
            valid_from=NOW - timedelta(days=1),
            confirmed_at=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=60),
            evidence_ids=[PROOF],
            impact_analysis={"reference_snapshots": deepcopy(refs)},
        ),
    )
    version = PolicyVersion(
        id=VERSION,
        user_id=USER,
        policy_id=POLICY,
        version_number=1,
        configuration=old.model_dump(mode="json"),
        content_hash=configuration_hash(old.model_dump(mode="json")),
        evidence_ids=[str(PROOF)],
        confirmed_at=NOW - timedelta(days=1),
        valid_from=NOW - timedelta(days=1),
    )
    policy = Policy(id=POLICY, user_id=USER)
    user = User(id=USER, is_simulated=True, timezone="UTC")
    rows = {Account: source, EvidenceItem: proof, Policy: policy, User: user}
    fake = SimpleNamespace(get=lambda model, _id: rows[model], scalar=lambda _query: version)
    for name in ("_read_snapshot", "validate_bank_projection"):
        monkeypatch.setattr(service, name, lambda *_args: None)
    monkeypatch.setattr(
        service, "current_audit_epoch", lambda *_args: SimpleNamespace(id=EPOCH, status="OPEN")
    )
    monkeypatch.setattr(
        service, "verify_audit_chain", lambda *_args: SimpleNamespace(status="VALID")
    )
    monkeypatch.setattr(service, "read_full_policy", lambda *_args: full_view)
    monkeypatch.setattr(service, "is_version_authorized", lambda *_args: True)
    monkeypatch.setattr(service, "_counterparty_binding", lambda *_args: PROOF)
    monkeypatch.setattr(service, "_current_payee_sources", lambda *_args: {PROOF, UUID(int=9701)})
    monkeypatch.setattr(service, "_evidence", lambda *_args, **_kwargs: [proof])
    body = PaymentScopeRequest.model_validate(
        {key: value for key, value in start_body().items() if key != "idempotency_key"}
    )
    original_full = deepcopy(full_view.current_version.impact_analysis)
    if mutate_original:
        proof.content = proof.content | {"counterparty_ref": "different"}
        proof.content_hash = configuration_hash(proof.content)
        with pytest.raises(PolicyLifecycleError) as rejected:
            service._scope(cast(Session, fake), USER, body, NOW, pinned_payee=PROOF)
        assert rejected.value.code == "PAYMENT_SOURCE_UNKNOWN"
    else:
        actual, _ = service._scope(cast(Session, fake), USER, body, NOW, pinned_payee=PROOF)
        assert actual.payee_evidence_id == PROOF and actual.full_planning_bank_authority is False
    assert full_view.planning_confirmation_valid is False
    assert full_view.reference_validation == "CHANGED_OR_UNAVAILABLE"
    assert full_view.current_version.impact_analysis == original_full


@pytest.mark.parametrize("current", ["CURRENT", "UNKNOWN", "STALE"])
def test_projection_binding_requires_a_current_verified_relation_and_never_changes_full_flags(
    monkeypatch: pytest.MonkeyPatch,
    current: str,
) -> None:
    """Projection-only bridge with original verification doubles, no computed funds."""
    from app.domain.full_payment_permissions import payment_command_identity, payment_scope_hash
    from app.services.full_payment_permissions import PaymentCommandOriginal

    initiation = original()
    request = {
        "start_command_id": str(initiation.command_id),
        "confirmation": {
            "expected_epoch_id": str(EPOCH),
            "reviewed_scope_hash": payment_scope_hash(initiation.scope),
            "accepted": True,
            "reason": "explicit",
            "idempotency_key": "projection-confirm",
        },
    }
    started = PaymentCommandOriginal(
        command_id=payment_command_identity(USER, EPOCH, "projection-confirm"),
        user_id=USER,
        epoch_id=EPOCH,
        kind="CONFIRM",
        idempotency_key="projection-confirm",
        start_command_id=initiation.command_id,
        original_request=request,
        request_hash=configuration_hash(
            {"user_id": str(USER), "kind": "CONFIRM", "request": request}
        ),
        principal_at_command=principal(),
        scope=initiation.scope,
        scope_hash=initiation.scope_hash,
        recorded_at=NOW,
    )
    full = SimpleNamespace(
        policy_id=FULL,
        template_name="PeriodicTransferPolicy",
        effective_status="ACTIVE",
        reference_validation="CHANGED_OR_UNAVAILABLE",
        planning_confirmation_valid=False,
        current_version=SimpleNamespace(
            version_id=FULL_VERSION, content_hash=started.scope.full_configuration_hash
        ),
    )
    saved = SimpleNamespace(content=started.model_dump(mode="json"))
    receipt = SimpleNamespace(
        original=started, current_scope_status=current, evidence_id=PROOF, evidence_hash="a" * 64
    )
    fake = SimpleNamespace(scalars=lambda _query: [saved])

    @contextmanager
    def local_scope(_session: Any) -> Any:
        yield

    monkeypatch.setattr(service, "_read_snapshot", lambda *_args: None)
    monkeypatch.setattr(service, "audit_read_scope", local_scope)
    monkeypatch.setattr(service, "_original", lambda *_args, **_kwargs: receipt)
    monkeypatch.setattr(service, "_scope", lambda *_args, **_kwargs: (started.scope, []))
    result = service.verified_periodic_transfer_projection_binding(
        cast(Session, fake), USER, cast(Any, full), NOW
    )
    assert (
        result.status
        == {
            "CURRENT": "VERIFIED_CURRENT_RELATION",
            "UNKNOWN": "UNKNOWN",
            "STALE": "NO_CURRENT_DEDICATED_RELATION",
        }[current]
    )
    assert result.bank_authority is result.changes_original_full_hash_or_flags is False
    assert full.planning_confirmation_valid is False


def test_original_preparation_exact_request_and_action_key_cannot_change_after_rehash() -> None:
    from app.domain.full_payment_permissions import (
        PaymentPrepareRequest,
        original_payment_action_key,
    )
    from app.services.full_payment_permissions import PaymentPreparationOriginal

    body = PaymentPrepareRequest(
        expected_epoch_id=EPOCH, period="2026-10", idempotency_key="original"
    )
    prepared = PaymentPreparationOriginal(
        user_id=USER,
        epoch_id=EPOCH,
        authorization_id=ACTION,
        authorization_evidence_hash="a" * 64,
        request=body,
        full_payment_action_key=original_payment_action_key(ACTION, "original"),
        principal_at_prepare=principal(),
        recorded_at=NOW,
    )
    assert prepared.request == body
    with pytest.raises(ValueError):
        PaymentPreparationOriginal.model_validate(
            prepared.model_dump() | {"full_payment_action_key": "different"}
        )


def test_committed_old_action_with_pending_full_prepare_original_is_never_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.models import ActionPlan

    @contextmanager
    def reader(_engine: Any) -> Any:
        yield SimpleNamespace(
            get=lambda model, _id: (
                SimpleNamespace(idempotency_key="original") if model is ActionPlan else None
            ),
            scalar=lambda _query: UUID(int=9750),
        )

    monkeypatch.setattr(service, "_reader", reader)
    with pytest.raises(PolicyLifecycleError) as rejected:
        service.enforce_full_payment_bank_scope(cast(Engine, None), USER, effect(), NOW)
    assert rejected.value.code == "PAYMENT_BINDING_NOT_FINAL"


@pytest.mark.parametrize("status", ["PLANNED", "AUTHORIZED"])
@pytest.mark.parametrize(
    "occupied",
    [None, "bank_count", "receipt_count", "posting_count", "claim_count", "legacy_count"],
)
def test_changed_full_scope_invalidates_only_complete_unsubmitted_zero_originals(
    status: str,
    occupied: str | None,
) -> None:
    counts = {
        "bank_count": 0,
        "receipt_count": 0,
        "posting_count": 0,
        "claim_count": 0,
        "legacy_count": 0,
    }
    if occupied is not None:
        counts[occupied] = 1
    result = service.payment_recheck_disposition(
        status, scope_changed=True, verified_settlement=False, **counts
    )
    assert result == ("INVALIDATE" if occupied is None else "INFLIGHT")
    assert (
        service.payment_recheck_disposition(
            status,
            scope_changed=False,
            bank_count=0,
            receipt_count=0,
            posting_count=0,
            claim_count=0,
            legacy_count=0,
            verified_settlement=False,
        )
        == "RETAIN"
    )


@pytest.mark.parametrize("status", ["SUBMITTED", "UNKNOWN", "SUCCEEDED", "RECONCILED"])
def test_recheck_does_not_replace_inflight_keys_or_call_strings_actual_settlement(
    status: str,
) -> None:
    assert (
        service.payment_recheck_disposition(
            status,
            scope_changed=True,
            bank_count=0,
            receipt_count=0,
            posting_count=0,
            claim_count=0,
            legacy_count=0,
            verified_settlement=False,
        )
        == "INFLIGHT"
    )
    if status in {"SUCCEEDED", "RECONCILED"}:
        assert (
            service.payment_recheck_disposition(
                status,
                scope_changed=True,
                bank_count=1,
                receipt_count=1,
                posting_count=3,
                claim_count=2,
                legacy_count=0,
                verified_settlement=True,
            )
            == "TERMINAL"
        )
