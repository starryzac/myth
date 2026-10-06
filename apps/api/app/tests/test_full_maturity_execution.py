"""Direct synthetic proof/hook risks; none is actual banking or FULL acceptance."""

from datetime import timedelta
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid5

import pytest
from app.db.models import ActionPlan
from app.domain.boundary_types import SourceIssue
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence
from app.domain.full_maturity_execution import (
    ALGORITHM,
    CONSENT_SOURCE,
    GUARDS_VERSION,
    MARKER,
    FullMaturityConfirmation,
    FullMaturityConsent,
    FullMaturityInput,
    FullMaturityRequest,
    derive_maturity_proof,
    maturity_action_payload,
    maturity_bank_key,
    verify_frozen_maturity_trace,
    verify_maturity_consent,
)
from app.domain.full_protection_projection import FullProtectionProjectionInput
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import configuration_hash
from app.services import full_maturity_execution as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_recovery_planning import fixed_holding, inputs
from app.tests.test_recovery import NOW, USER
from pydantic import ValidationError
from sqlalchemy.orm import Session


def fixture() -> FullMaturityInput:
    plan = inputs([fixed_holding(early=False)])
    position_id = plan.positions[0].position_id
    linked = plan.linked_asset_policy
    config = {
        **linked.configuration,
        "allowed_asset_classes": ["FIXED_DEPOSIT"],
        "max_lock_days": 30,
    }
    plan = plan.model_copy(
        update={
            "linked_asset_policy": linked.model_copy(
                update={
                    "configuration": config,
                    "content_hash": configuration_hash(config),
                }
            )
        }
    )
    issues = [
        SourceIssue(
            code="UNRECONCILED_POSITION_AVAILABILITY",
            entity_type="source",
            entity_id=str(position_id),
        )
    ]
    plan = plan.model_copy(
        update={
            "snapshot": plan.snapshot.model_copy(
                update={
                    "source_issues": issues,
                }
            ),
            "positions": [plan.positions[0].model_copy(update={"principal_available_at": NOW})],
        }
    )
    body = FullMaturityRequest(
        policy_id=plan.policy_id,
        expected_version_id=plan.policy_version_id,
        expected_epoch_id=UUID(int=981),
        position_id=position_id,
        idempotency_key="SYNTHETIC_MATURE",
    )
    return FullMaturityInput(
        user_id=USER,
        epoch_id=body.expected_epoch_id,
        request=body,
        as_of=NOW,
        current_effective_status="CONFIRMED",
        current_reference_validation="CURRENT",
        planning=plan,
        protection=FullProtectionProjectionInput(
            snapshot=plan.snapshot,
            positions=plan.positions,
            boundary_versions=plan.boundary_versions,
            boundary_products=plan.boundary_products,
            policies=[],
        ),
        current_mvp_authorized=True,
        independent_bank_principal_cents=plan.positions[0].principal_cents,
        independent_bank_head={"SYNTHETIC": True},
        source_evidence_ids=[UUID(int=9001)],
        source_issues=[("UNRECONCILED_POSITION_AVAILABILITY", str(position_id))],
        source_originals=[{"SYNTHETIC_SCOPE_ONLY": True}],
        full_original={},
        current_mvp_original={},
        position_original={"SYNTHETIC": True},
        catalogue_original={},
        original_full_projection={},
    )


def test_whole_maturity_is_conditional_same_owner_and_frozen_1098_protection() -> None:
    data = fixture()
    proof = derive_maturity_proof(data)
    assert proof.status == "VERIFIED_SCOPE", proof.reasons
    assert proof.command is not None and proof.command.kind == "MATURE"
    assert proof.command.principal_cents == data.independent_bank_principal_cents
    assert proof.command.requested_at == proof.command.available_at == NOW
    assert proof.command.expires_at == NOW + timedelta(minutes=15)
    assert proof.candidate_denominator == len(data.planning.holdings) == 1
    assert proof.conditional_protection_hash is not None
    assert not proof.projected_cash_is_current_cash and not proof.bank_receipt_proven
    assert derive_maturity_proof(data) == proof
    later = data.model_copy(
        update={
            "as_of": NOW + timedelta(seconds=3),
            "planning": data.planning.model_copy(
                update={
                    "snapshot": data.planning.snapshot.model_copy(
                        update={"as_of": NOW + timedelta(seconds=3)}
                    )
                }
            ),
            "protection": data.protection.model_copy(
                update={
                    "snapshot": data.protection.snapshot.model_copy(
                        update={"as_of": NOW + timedelta(seconds=3)}
                    )
                }
            ),
        }
    )
    actual = derive_maturity_proof(later, proof.command)
    assert actual.status == "VERIFIED_SCOPE", actual.reasons
    assert actual.command == proof.command and actual.command_hash == proof.command_hash
    assert data.planning.snapshot.source_issues  # Original UNKNOWN is retained, never relabelled.


@pytest.mark.parametrize(
    "change",
    [
        {"current_mvp_authorized": False},
        {"current_effective_status": "REVOKED"},
        {"current_reference_validation": "CHANGED_OR_UNAVAILABLE"},
        {"independent_bank_principal_cents": 49999},
        {"source_issues": [("UNRECONCILED_POSITION_AVAILABILITY", str(UUID(int=91)))]},
        {"source_issues": [("INVALID_ASSET_EXPOSURE", "bank")]},
        {"source_evidence_ids": []},
        {"source_evidence_ids": [UUID(int=1), UUID(int=1)]},
    ],
)
def test_changed_authority_unmatched_bank_or_other_unknown_never_ready(
    change: dict[str, Any],
) -> None:
    proof = derive_maturity_proof(fixture().model_copy(update=change))
    assert proof.status != "VERIFIED_SCOPE" and proof.reasons


@pytest.mark.parametrize(
    "change",
    [
        {"position_id": UUID(int=4)},
        {"expected_epoch_id": UUID(int=4)},
        {"expected_version_id": UUID(int=4)},
        {"policy_id": UUID(int=4)},
    ],
)
def test_complete_request_identity_cannot_be_rebound(change: dict[str, Any]) -> None:
    data = fixture()
    proof = derive_maturity_proof(
        data.model_copy(update={"request": data.request.model_copy(update=change)})
    )
    assert proof.status != "VERIFIED_SCOPE"


@pytest.mark.parametrize("kind", ["EARLY_WITHDRAW", "REDEEM"])
def test_early_or_liquid_quote_cannot_be_renamed_maturity(kind: str) -> None:
    data = fixture()
    holding = data.planning.holdings[0]
    quote = holding.original.quote
    assert quote is not None
    changed = holding.model_copy(
        update={
            "original": holding.original.model_copy(
                update={"quote": quote.model_copy(update={"kind": kind})}
            )
        }
    )
    assert (
        derive_maturity_proof(
            data.model_copy(
                update={"planning": data.planning.model_copy(update={"holdings": [changed]})}
            )
        ).status
        != "VERIFIED_SCOPE"
    )


def test_contract_cannot_default_zero_loss_or_extend_expiry() -> None:
    data = fixture()
    proof = derive_maturity_proof(data)
    assert proof.command is not None
    assert (
        derive_maturity_proof(
            data, proof.command.model_copy(update={"expires_at": NOW + timedelta(hours=1)})
        ).status
        == "BLOCKED"
    )
    holding = data.planning.holdings[0]
    rule = {**holding.original.product.maturity_rule, "principal_return_bps": 9999}
    bad = holding.model_copy(
        update={
            "original": holding.original.model_copy(
                update={
                    "product": holding.original.product.model_copy(update={"maturity_rule": rule})
                }
            )
        }
    )
    with pytest.raises(ValidationError):
        derive_maturity_proof(
            data.model_copy(
                update={"planning": data.planning.model_copy(update={"holdings": [bad]})}
            )
        )


def test_explicit_user_consent_is_not_role_status_or_latest_receipt() -> None:
    data = fixture()
    proof = derive_maturity_proof(data)
    assert proof.command_hash is not None
    principal = LocalActorPrincipal(
        user_id=USER,
        role="USER",
        session_id=UUID(int=3),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )
    body = FullMaturityConfirmation(
        expected_epoch_id=data.epoch_id, reviewed_command_hash=proof.command_hash, accepted=True
    )
    consent = FullMaturityConsent(
        user_id=USER,
        epoch_id=data.epoch_id,
        action_id=UUID(int=8),
        reviewed_command_hash=proof.command_hash,
        original_confirmation=body,
        principal_at_confirmation=principal,
        confirmed_at=NOW,
    )
    verify_maturity_consent(
        consent, UUID(int=8), USER, data.epoch_id, proof.command_hash, NOW, current=True
    )
    for changed in [
        consent.model_copy(update={"action_id": UUID(int=7)}),
        consent.model_copy(
            update={"principal_at_confirmation": principal.model_copy(update={"role": "AGENT"})}
        ),
    ]:
        with pytest.raises(ValueError):
            verify_maturity_consent(
                changed, UUID(int=8), USER, data.epoch_id, proof.command_hash, NOW, current=True
            )
    with pytest.raises(ValidationError):
        FullMaturityConfirmation.model_validate(
            {
                "expected_epoch_id": data.epoch_id,
                "reviewed_command_hash": proof.command_hash,
                "accepted": 1,
            }
        )


def test_missing_either_private_guard_is_explicitly_not_implemented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import asset_exposure_import, simulated_bank

    monkeypatch.setattr(
        simulated_bank, "FULL_MATURITY_GUARDS_VERSION", GUARDS_VERSION, raising=False
    )
    monkeypatch.setattr(
        asset_exposure_import, "FULL_MATURITY_GUARDS_VERSION", "MISSING", raising=False
    )
    with pytest.raises(PolicyLifecycleError, match="接缝"):
        service.require_installed_guard()


def test_new_pending_exposure_requires_exact_no_effect_and_preserves_bank_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = fixture()
    proof = derive_maturity_proof(data)
    command = proof.command
    assert command is not None
    action_id = uuid5(data.epoch_id, maturity_bank_key(data.request.idempotency_key))
    action = ActionPlan(
        id=action_id,
        user_id=USER,
        decision_run_id=UUID(int=21),
        position_id=command.position_id,
        status="PLANNED",
        request={},
        request_hash="f" * 64,
        idempotency_key=maturity_bank_key(data.request.idempotency_key),
    )
    monkeypatch.setattr(service, "_frozen", lambda *_: (data, proof, command))
    monkeypatch.setattr(service, "row_copy", lambda row: {"SYNTHETIC": True})
    monkeypatch.setattr(
        service,
        "ledger_heads",
        lambda *_: {
            "POSITION:" + str(command.position_id): type(
                "SyntheticHead", (), {"balance_after_cents": command.principal_cents}
            )()
        },
    )
    double = MagicMock(spec=Session)
    double.get.return_value = object()
    session = cast(Session, double)
    declaration = service._declaration(action)
    assert service.validate_maturity_exposure_original(
        session, action, declaration, [], [], [], [], [], NOW
    )
    with pytest.raises(PolicyLifecycleError):
        service.validate_maturity_exposure_original(
            session, action, {**declaration, "state": "NO_EFFECT"}, [], [], [], [], [], NOW
        )
    bank = type("SyntheticBank", (), {"action_plan_id": action.id})()
    assert not service.validate_maturity_exposure_original(
        session, action, declaration, [bank], [], [], [], [], NOW
    )


def history_fixture(phase: str) -> DecisionTrace:
    """SYNTHETIC pure historical input: never a database/bank execution proof."""
    data = fixture()
    source_content = {"SYNTHETIC_PURE_HISTORY_FIXTURE": True}
    source = TraceEvidence(
        id=data.source_evidence_ids[0],
        user_id=USER,
        evidence_level="BANK_CONFIRMED",
        source_type="SYNTHETIC_HISTORY_INPUT",
        source_ref="SYNTHETIC_TOOL_ONLY",
        content=source_content,
        content_hash=configuration_hash(source_content),
        captured_content_hash=configuration_hash(source_content),
        content_integrity="VERIFIED",
        status_at_decision="VALID",
        observed_at=NOW,
        valid_from=NOW,
    )
    data = data.model_copy(update={"source_originals": [source.model_dump(mode="json")]})
    proof = derive_maturity_proof(data)
    assert proof.status == "VERIFIED_SCOPE" and proof.command is not None
    assert proof.command_hash is not None
    action_id = uuid5(data.epoch_id, maturity_bank_key(data.request.idempotency_key))
    payload = maturity_action_payload(data, proof, action_id)
    base: dict[str, Any] = dict(
        user_id=USER,
        action_id=action_id,
        phase=phase,
        as_of=NOW,
        algorithm_versions={"trace": "decision-trace-v1", MARKER: ALGORITHM},
        policies=[],
    )
    if phase == "PREPARE":
        return build_trace(
            **base,
            run_id=uuid5(action_id, "maturity-user-prepare"),
            inputs={"maturity_input": data.model_dump(mode="json"), "action_request": payload},
            sources=[source],
            outcome={"maturity_proof": proof.model_dump(mode="json")},
        )
    confirmation = FullMaturityConfirmation(
        expected_epoch_id=data.epoch_id,
        reviewed_command_hash=proof.command_hash,
        accepted=True,
    )
    consent = FullMaturityConsent(
        user_id=USER,
        epoch_id=data.epoch_id,
        action_id=action_id,
        reviewed_command_hash=proof.command_hash,
        original_confirmation=confirmation,
        confirmed_at=NOW,
        principal_at_confirmation=LocalActorPrincipal(
            user_id=USER,
            role="USER",
            session_id=UUID(int=3),
            issued_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=10),
        ),
    )
    consent_content = consent.model_dump(mode="json")
    consent_source = TraceEvidence(
        id=uuid5(action_id, "maturity-user-consent"),
        user_id=USER,
        evidence_level="USER_CONFIRMED_ACTION",
        source_type=CONSENT_SOURCE,
        source_ref=str(action_id),
        content=consent_content,
        content_hash=configuration_hash(consent_content),
        captured_content_hash=configuration_hash(consent_content),
        content_integrity="VERIFIED",
        status_at_decision="VALID",
        observed_at=NOW,
        valid_from=NOW,
        valid_to=proof.command.expires_at,
    )
    base["parent_run_id"] = uuid5(action_id, "maturity-user-prepare")
    if phase == "CONFIRM":
        return build_trace(
            **base,
            run_id=uuid5(action_id, "maturity-user-confirmation-trace"),
            inputs={
                "confirmation": confirmation.model_dump(mode="json"),
                "action_request": payload,
                "bank_key": maturity_bank_key(data.request.idempotency_key),
            },
            sources=[consent_source],
            outcome={"maturity_user_consent": consent_content},
        )
    assert phase == "BANK_ACCEPT"
    return build_trace(
        **base,
        run_id=uuid5(action_id, "legacy-bank-accept-decision"),
        inputs={
            "action_request": payload,
            "bank_request": proof.command.model_dump(mode="json"),
            "validation_inputs": {
                "full_maturity_validation": {
                    "action_id": str(action_id),
                    "inputs": data.model_dump(mode="json"),
                    "proof": proof.model_dump(mode="json"),
                    "original_consent": consent_content,
                    "consent_source": consent_source.model_dump(mode="json"),
                }
            },
        },
        sources=[source, consent_source],
        outcome={
            "autonomy_level": "ASK_ONCE",
            "new_authority": False,
            "settlement_kind": "ORIGINAL_CONTRACT",
            "bank_validation_status": "READY",
        },
    )


def rebuilt(trace: DecisionTrace, **updates: Any) -> DecisionTrace:
    fields = trace.model_dump(exclude={"input_hash", "trace_hash"})
    return build_trace(**{**fields, **updates})


@pytest.mark.parametrize("phase", ["PREPARE", "CONFIRM", "BANK_ACCEPT"])
def test_pure_historical_protocol_reproduces_complete_originals(phase: str) -> None:
    trace = history_fixture(phase)
    verify_frozen_maturity_trace(trace)
    with pytest.raises(ValueError):
        verify_frozen_maturity_trace(rebuilt(trace, parent_run_id=UUID(int=6)))
    changed = trace.inputs.copy()
    changed["action_request"] = {**changed["action_request"], "invented_authority": True}
    with pytest.raises(ValueError):
        verify_frozen_maturity_trace(rebuilt(trace, inputs=changed))


@pytest.mark.parametrize(
    "field", ["inputs", "proof", "original_consent", "consent_source", "action_id"]
)
def test_bank_accept_missing_closed_member_is_not_historical_success(field: str) -> None:
    trace = history_fixture("BANK_ACCEPT")
    changed = trace.inputs.copy()
    validation = changed["validation_inputs"]["full_maturity_validation"].copy()
    validation.pop(field)
    changed["validation_inputs"] = {"full_maturity_validation": validation}
    with pytest.raises(ValueError):
        verify_frozen_maturity_trace(rebuilt(trace, inputs=changed))


@pytest.mark.parametrize(
    "change", ["denominator", "authority", "proof", "current-user", "fresh-time"]
)
def test_bank_accept_rehashed_false_claim_cannot_replace_fresh_full_validation(change: str) -> None:
    trace = history_fixture("BANK_ACCEPT")
    changed = trace.inputs.copy()
    validation = changed["validation_inputs"]["full_maturity_validation"].copy()
    if change == "denominator":
        validation["inputs"] = {**validation["inputs"], "source_originals": []}
    elif change == "authority":
        validation["inputs"] = {**validation["inputs"], "current_mvp_authorized": False}
    elif change == "proof":
        validation["proof"] = {**validation["proof"], "remaining_negative_checkpoints": 99}
    elif change == "current-user":
        consent = validation["original_consent"].copy()
        consent["principal_at_confirmation"] = {
            **consent["principal_at_confirmation"],
            "role": "AGENT",
        }
        validation["original_consent"] = consent
    else:
        validation["inputs"] = {
            **validation["inputs"],
            "as_of": (NOW + timedelta(seconds=1)).isoformat(),
        }
    changed["validation_inputs"] = {"full_maturity_validation": validation}
    with pytest.raises(ValueError):
        verify_frozen_maturity_trace(rebuilt(trace, inputs=changed))


def test_bank_accept_original_consent_source_cannot_be_retimed() -> None:
    trace = history_fixture("BANK_ACCEPT")
    copied = trace.inputs.copy()
    validation = copied["validation_inputs"]["full_maturity_validation"].copy()
    source = TraceEvidence.model_validate_json(
        __import__("json").dumps(validation["consent_source"])
    )
    source = source.model_copy(update={"observed_at": NOW + timedelta(seconds=1)})
    validation["consent_source"] = source.model_dump(mode="json")
    copied["validation_inputs"] = {"full_maturity_validation": validation}
    with pytest.raises(ValueError):
        verify_frozen_maturity_trace(
            rebuilt(trace, inputs=copied, sources=[trace.sources[0], source])
        )
