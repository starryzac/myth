"""Direct finite-state and consent contracts; no database or financial claim."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.db.full_models import FullPolicy, FullPolicyVersion
from app.db.models import AuditEpoch, EvidenceItem, User
from app.domain.full_policy_configuration import TemplateName
from app.domain.policy_configuration import configuration_hash
from app.services import full_policy_lifecycle as full
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_policy_configuration import EXAMPLES
from pydantic import ValidationError

NOW = datetime(2026, 10, 5, tzinfo=UTC)


def confirm_body() -> dict[str, Any]:
    canonical = full.canonical_candidate("InterventionPolicy", EXAMPLES["InterventionPolicy"])
    return {
        "template_name": "InterventionPolicy",
        "configuration": canonical,
        "reviewed_hash": configuration_hash(canonical),
        "accepted": True,
        "reason": "用户明确审核配置",
        "idempotency_key": "full-user-confirmation-1",
    }


@pytest.mark.parametrize("template", full.FULL_TEMPLATES)
def test_reviewed_eight_templates_have_exact_hash_and_no_bank_grant(template: TemplateName) -> None:
    original = deepcopy(EXAMPLES[template])
    canonical = full.canonical_candidate(template, original)
    assert (
        full.reviewed_candidate(template, original, configuration_hash(canonical), True)
        == canonical
    )
    assert original == EXAMPLES[template]
    with pytest.raises(PolicyLifecycleError) as mismatch:
        full.reviewed_candidate(template, original, "f" * 64, True)
    assert mismatch.value.code == "REVIEW_MISMATCH"
    with pytest.raises(PolicyLifecycleError) as denied:
        full.reviewed_candidate(template, original, configuration_hash(canonical), False)
    assert denied.value.code == "CONFIRMATION_REQUIRED"


@pytest.mark.parametrize(
    ("template", "code"),
    [
        ("RecurringObligationPolicy", "USE_MVP_LIFECYCLE"),
        ("LivingReservePolicy", "USE_MVP_LIFECYCLE"),
        ("EmergencyBufferPolicy", "USE_MVP_LIFECYCLE"),
        ("LongTermGoalPolicy", "USE_FULL_GOAL_BRIDGE"),
    ],
)
def test_original_templates_do_not_silently_enter_new_storage(
    template: TemplateName, code: str
) -> None:
    with pytest.raises(PolicyLifecycleError) as captured:
        full.canonical_candidate(template, EXAMPLES[template])
    assert captured.value.code == code


@pytest.mark.parametrize("field", ["owner", "now", "authority", "confirmation", "success"])
def test_unknown_or_financial_override_fields_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        full.FullCreateRequest.model_validate({**confirm_body(), field: True})
    with pytest.raises(PolicyLifecycleError):
        full.canonical_candidate(
            "InterventionPolicy", {**EXAMPLES["InterventionPolicy"], field: True}
        )


@pytest.mark.parametrize("accepted", [False, 1, "true", None])
def test_api_needs_actual_boolean_explicit_confirmation(accepted: Any) -> None:
    with pytest.raises(ValidationError):
        full.FullCreateRequest.model_validate({**confirm_body(), "accepted": accepted})


@pytest.mark.parametrize("field", ["reason", "idempotency_key"])
def test_blank_command_metadata_is_not_a_valid_request(field: str) -> None:
    with pytest.raises(ValidationError):
        full.FullCreateRequest.model_validate({**confirm_body(), field: "  "})


@pytest.mark.parametrize(
    ("state", "confirmed", "start", "end", "expected"),
    [
        ("ACTIVE", NOW, NOW, None, "ACTIVE"),
        ("ACTIVE", NOW, NOW + timedelta(days=1), None, "CONFIRMED"),
        ("CONFIRMED", NOW, NOW, None, "ACTIVE"),
        ("ACTIVE", NOW, NOW, NOW, "EXPIRED"),
        ("SUSPENDED", NOW, NOW, NOW, "EXPIRED"),
        ("SUSPENDED", NOW, NOW, None, "SUSPENDED"),
        ("REVOKED", NOW, NOW, None, "REVOKED"),
        ("EXPIRED", NOW, NOW, None, "EXPIRED"),
        ("ACTIVE", NOW + timedelta(seconds=1), NOW, None, "CONFIRMATION_IN_FUTURE"),
    ],
)
def test_exact_clock_boundaries_and_current_confirmation(
    state: full.PolicyState,
    confirmed: datetime,
    start: datetime,
    end: datetime | None,
    expected: str,
) -> None:
    assert full.derived_state(state, confirmed, start, end, NOW) == expected


@pytest.mark.parametrize("field", ["now", "confirmed", "start", "end"])
def test_naive_times_cannot_form_authority(field: str) -> None:
    values = {"now": NOW, "confirmed": NOW, "start": NOW, "end": NOW + timedelta(days=1)}
    values[field] = values[field].replace(tzinfo=None)
    with pytest.raises(PolicyLifecycleError) as captured:
        full.derived_state(
            "ACTIVE", values["confirmed"], values["start"], values["end"], values["now"]
        )
    assert captured.value.code == "INVALID_CLOCK"


@pytest.mark.parametrize(
    ("kind", "current", "intended", "expected"),
    [
        ("CREATE", "PROPOSED", "ACTIVE", "ACTIVE"),
        ("CREATE", "PROPOSED", "CONFIRMED", "CONFIRMED"),
        ("SUSPEND", "ACTIVE", None, "SUSPENDED"),
        ("SUSPEND", "SUSPENDED", None, "SUSPENDED"),
        ("REVOKE", "SUSPENDED", None, "REVOKED"),
        ("REVOKE", "REVOKED", None, "REVOKED"),
        ("RESUME", "SUSPENDED", "ACTIVE", "ACTIVE"),
        ("CHANGE", "SUSPENDED", "ACTIVE", "SUSPENDED"),
        ("CHANGE", "ACTIVE", "CONFIRMED", "CONFIRMED"),
        ("REFRESH_TIME", "EXPIRED", "EXPIRED", "EXPIRED"),
    ],
)
def test_finite_legal_state_commands(
    kind: full.CommandKind, current: str, intended: str | None, expected: str
) -> None:
    assert full.next_state(kind, current, intended) == expected


@pytest.mark.parametrize("current", ["REVOKED", "EXPIRED", "CONFIRMATION_IN_FUTURE", "ARCHIVED"])
@pytest.mark.parametrize("kind", ["SUSPEND", "RESUME", "CHANGE"])
def test_terminated_future_or_archived_state_cannot_be_revived(
    current: str, kind: full.CommandKind
) -> None:
    with pytest.raises(PolicyLifecycleError) as captured:
        full.next_state(kind, current, "ACTIVE")
    assert captured.value.code == "INVALID_POLICY_STATE"


def test_response_cannot_claim_bank_authority_or_current_authority_from_receipt() -> None:
    values = {
        "policy_id": UUID(int=1),
        "epoch_id": UUID(int=2),
        "version_id": UUID(int=3),
        "command_id": UUID(int=4),
        "status": "ACTIVE",
        "configuration_hash": "a" * 64,
    }
    result = full.FullLifecycleResult.model_validate(values)
    assert result.bank_authority is False
    assert result.receipt_is_current_authority is False
    assert result.action_dependencies_supported is False
    for field in ("bank_authority", "receipt_is_current_authority", "dedicated_audit_event"):
        with pytest.raises(ValidationError):
            full.FullLifecycleResult.model_validate({**values, field: True})


def test_semantic_change_fields_keep_missing_value_changes() -> None:
    assert full.changed_fields({"a": 1, "b": 2}, {"a": 2, "c": 3}) == ["a", "b", "c"]
    assert full.changed_fields({"nullable": None}, {}) == ["nullable"]


@pytest.mark.parametrize("digest", ["☃" * 64, "a" * 63, "A" * 64])
def test_invalid_review_digest_is_a_domain_rejection(digest: str) -> None:
    with pytest.raises(PolicyLifecycleError) as captured:
        full.reviewed_candidate("InterventionPolicy", EXAMPLES["InterventionPolicy"], digest, True)
    assert captured.value.code == "REVIEW_MISMATCH"


def original_confirmation_rows() -> tuple[FullPolicy, FullPolicyVersion, EvidenceItem, AuditEpoch]:
    canonical = full.canonical_candidate("InterventionPolicy", EXAMPLES["InterventionPolicy"])
    policy = FullPolicy(
        id=UUID(int=501),
        user_id=UUID(int=502),
        epoch_id=UUID(int=503),
        template_name="InterventionPolicy",
        dsl_version="FULL_V1",
    )
    confirmation = {
        "protocol": full.PROTOCOL,
        "user_id": str(policy.user_id),
        "epoch_id": str(policy.epoch_id),
        "policy_id": str(policy.id),
        "version_id": str(UUID(int=504)),
        "template_name": policy.template_name,
        "reviewed_hash": configuration_hash(canonical),
        "confirmed_at": NOW.isoformat(),
        "accepted": True,
        "bank_authority": False,
        "confirmation_evidence_id": str(UUID(int=505)),
        "request_key": "explicit-confirmation",
        "request_hash": "a" * 64,
    }
    version = FullPolicyVersion(
        id=UUID(int=504),
        user_id=policy.user_id,
        policy_id=policy.id,
        created_at=NOW,
        confirmed_at=NOW,
        valid_from=NOW,
        valid_until=None,
        configuration=canonical,
        content_hash=configuration_hash(canonical),
        confirmation=confirmation,
        evidence_ids=[str(UUID(int=505))],
        version_number=1,
        previous_hash=None,
        summary="原确认配置",
        change_reason="用户明确确认",
        impact_analysis={"reference_snapshots": []},
    )
    proof = EvidenceItem(
        id=UUID(int=505),
        user_id=policy.user_id,
        source_type=full.CONFIRMATION_SOURCE,
        evidence_level="USER_CONFIRMED_POLICY",
        source_ref=str(version.id),
        status="VALID",
        content=deepcopy(confirmation),
        content_hash=configuration_hash(confirmation),
        observed_at=NOW,
        valid_from=NOW,
    )
    return (
        policy,
        version,
        proof,
        AuditEpoch(id=policy.epoch_id, user_id=policy.user_id, status="OPEN"),
    )


class OriginalRowsSession:
    def __init__(self, policy: FullPolicy, proof: EvidenceItem | None, epoch: AuditEpoch) -> None:
        self.user = User(id=policy.user_id, is_simulated=True, timezone="UTC")
        self.proof, self.epoch = proof, epoch

    def get(self, model: Any, identifier: UUID) -> Any:
        return {User: self.user, EvidenceItem: self.proof, AuditEpoch: self.epoch}[model]


@pytest.mark.parametrize(
    "case",
    [
        "wrong_owner",
        "wrong_source",
        "wrong_hash",
        "coerced_acceptance",
        "extra_grant",
        "future_confirmed",
    ],
)
def test_current_original_confirmation_rejects_precise_source_tampering(case: str) -> None:
    policy, version, proof, epoch = original_confirmation_rows()
    if case == "wrong_owner":
        proof.user_id = UUID(int=999)
    elif case == "wrong_source":
        proof.source_type = "MODEL_INFERRED"
    elif case == "wrong_hash":
        proof.content_hash = "f" * 64
    elif case == "coerced_acceptance":
        version.confirmation = {**version.confirmation, "accepted": 1}
    elif case == "extra_grant":
        version.confirmation = {**version.confirmation, "grant": True}
    elif case == "future_confirmed":
        version.confirmed_at = NOW + timedelta(seconds=1)
    with pytest.raises(PolicyLifecycleError) as captured:
        full._verify_version(OriginalRowsSession(policy, proof, epoch), policy, version, NOW)  # type: ignore[arg-type]
    assert captured.value.code == "INVALID_FULL_POLICY_SOURCE"


def test_open_missing_evidence_rejects_and_sealed_retained_bytes_do_not_claim_archive_proof() -> (
    None
):
    policy, version, proof, epoch = original_confirmation_rows()
    full._verify_version(OriginalRowsSession(policy, proof, epoch), policy, version, NOW)  # type: ignore[arg-type]
    with pytest.raises(PolicyLifecycleError):
        full._verify_version(OriginalRowsSession(policy, None, epoch), policy, version, NOW)  # type: ignore[arg-type]
    epoch.status = "SEALED"
    full._verify_version(OriginalRowsSession(policy, None, epoch), policy, version, NOW)  # type: ignore[arg-type]
    view = full._version_view(OriginalRowsSession(policy, None, epoch), version)  # type: ignore[arg-type]
    assert view.confirmation_evidence_status == "RETAINED_IN_VERSION_CURRENT_EVIDENCE_MISSING"
    assert view.bank_authority is False and view.dedicated_audit_event is False


def test_recoverable_original_request_envelope_contains_actual_owner_and_original_body() -> None:
    request = full.FullCreateRequest.model_validate(confirm_body())
    user = UUID(int=511)
    original = full._request("CREATE", user, None, request)
    assert original == {
        "protocol": "full-policy-command-v1",
        "kind": "CREATE",
        "user_id": str(user),
        "policy_id": None,
        "body": request.model_dump(mode="json"),
    }
    assert original["body"]["idempotency_key"] == request.idempotency_key
