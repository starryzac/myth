"""Frozen adoption expiry proof; zero protection floor is never a cash credit or grant."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.domain.audit_chain_types import AuditVerification
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_protection_projection import FullProtectionPolicySource
from app.domain.full_seasonal_adoption import (
    MAX_ADOPTIONS,
    SOURCE,
    SeasonalAdoptionOriginal,
    verify_frozen_seasonal_adoption_trace,
)
from app.domain.policy_configuration import MoneyCents, configuration_hash
from pydantic import Field, StrictInt

ALGORITHM: Literal["seasonal-ended-adoption-proof-v1"] = "seasonal-ended-adoption-proof-v1"
REFERENCE_KIND = "VERIFIED_ENDED_SEASONAL_ADOPTION"
MAX_PROOF_BYTES = 16 * 1024 * 1024
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class EndedSeasonalAdoptionRecord(BoundaryModel):
    """Every registered row retains its original trace and actual current source rows."""

    adoption_evidence_original: dict[str, Any]
    trace: DecisionTrace
    current_evidence_originals: list[dict[str, Any]]


class EndedSeasonalAdoptionInput(BoundaryModel):
    protocol: Literal["seasonal-ended-adoption-input-v1"] = "seasonal-ended-adoption-input-v1"
    user_id: UUID
    epoch_id: UUID
    policy_id: UUID
    as_of: datetime
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    epoch_original: dict[str, Any]
    audit: AuditVerification
    current_policy_original: dict[str, Any]
    registered_adoption_evidence_count: Annotated[StrictInt, Field(ge=0)]
    registered_adoption_evidence_ids: list[UUID]
    records: Annotated[list[EndedSeasonalAdoptionRecord], Field(max_length=MAX_ADOPTIONS)]


class EndedSeasonalAdoptionProof(BoundaryModel):
    simulation: Literal[True] = True
    algorithm_version: Literal["seasonal-ended-adoption-proof-v1"] = ALGORITHM
    status: Literal["VERIFIED_ENDED", "NO_ORIGINAL_ADOPTION", "UNKNOWN"]
    user_id: UUID
    epoch_id: UUID
    policy_id: UUID
    as_of: datetime
    inputs: EndedSeasonalAdoptionInput | None
    input_hash: Digest | None
    registered_adoption_evidence_count: Annotated[StrictInt, Field(ge=0)] | None
    registered_adoption_evidence_ids: list[UUID]
    retained_command_ids: list[UUID]
    original: SeasonalAdoptionOriginal | None
    original_adopted_cents: MoneyCents | None
    protection_end: date | None
    floor_released_on: date | None
    current_floor_cents: Literal[0] | None
    reasons: list[str]
    proof_hash: Digest
    bank_authority: Literal[False] = False
    financial_execution_performed: Literal[False] = False
    cash_balance_changed: Literal[False] = False
    future_income_in_current_cash_cents: Literal[0] = 0
    current_permission_proven: Literal[False] = False


def _same_policy(
    original: SeasonalAdoptionOriginal, current: dict[str, Any], now: datetime
) -> None:
    prior = original.scope.full_policy_original
    # Only effective status and its derived current-planning bit may change with time.
    dynamic = {"effective_status", "planning_confirmation_valid"}
    current_comparison, prior_comparison = dict(current), dict(prior)
    for comparison in (current_comparison, prior_comparison):
        # These fields are declared DTO clocks. Compare their aware instants only;
        # keep both original JSON bodies unchanged, including every hash and proof.
        comparison["updated_at"] = datetime.fromisoformat(comparison["updated_at"])
        version_comparison = dict(comparison["current_version"])
        for key in ("confirmed_at", "valid_from", "valid_until"):
            if version_comparison[key] is not None:
                version_comparison[key] = datetime.fromisoformat(version_comparison[key])
        comparison["current_version"] = version_comparison
    if (
        {key: value for key, value in current_comparison.items() if key not in dynamic}
        != {key: value for key, value in prior_comparison.items() if key not in dynamic}
        or current["policy_id"] != str(original.policy_id)
        or current["epoch_id"] != str(original.epoch_id)
        or current["template_name"] != "SeasonalReservePolicy"
        or current["status"] not in {"ACTIVE", "CONFIRMED"}
        or current["reference_validation"] != "CURRENT"
        or current["current_version"]["confirmation_evidence_status"] != "CURRENT_EVIDENCE_MATCHED"
    ):
        raise ValueError("ENDED_ADOPTION_CURRENT_POLICY_VERSION_OR_CONFIRMATION_CHANGED")
    version = current["current_version"]
    start = datetime.fromisoformat(version["valid_from"])
    confirmed = datetime.fromisoformat(version["confirmed_at"])
    end = datetime.fromisoformat(version["valid_until"]) if version["valid_until"] else None
    if confirmed > now:
        raise ValueError("ENDED_ADOPTION_CURRENT_CONFIRMATION_IN_FUTURE")
    effective = (
        "EXPIRED" if end is not None and now >= end else ("CONFIRMED" if now < start else "ACTIVE")
    )
    if current["effective_status"] != effective or current["planning_confirmation_valid"] is not (
        effective != "EXPIRED"
    ):
        raise ValueError("ENDED_ADOPTION_CURRENT_POLICY_CLOCK_OR_STATE_NOT_EXACT")


def _record(record: EndedSeasonalAdoptionRecord, user_id: UUID) -> SeasonalAdoptionOriginal:
    original = verify_frozen_seasonal_adoption_trace(record.trace)
    raw = record.adoption_evidence_original
    if (
        original.user_id != user_id
        or raw["id"] != str(uuid5(original.command_id, "evidence"))
        or raw["user_id"] != str(user_id)
        or raw["source_type"] != SOURCE
        or raw["source_ref"] != str(original.command_id)
        or raw["evidence_level"] != "USER_CONFIRMED_POLICY"
        or raw["status"] != "VALID"
        or raw["content"] != original.model_dump(mode="json")
        or raw["content_hash"] != configuration_hash(raw["content"])
        or raw["valid_to"] is not None
        or any(
            datetime.fromisoformat(raw[key]) != original.recorded_at
            for key in ("created_at", "observed_at", "valid_from")
        )
    ):
        raise ValueError("ENDED_ADOPTION_ORIGINAL_METADATA_OR_HASH_CHANGED")
    expected = {row["id"]: row for row in original.scope.source_evidence_originals}
    expected[raw["id"]] = raw
    actual = {row["id"]: row for row in record.current_evidence_originals}
    if len(actual) != len(record.current_evidence_originals) or actual != expected:
        raise ValueError("ENDED_ADOPTION_CURRENT_ORIGINAL_SOURCE_DENOMINATOR_CHANGED")
    return original


def _audit(value: EndedSeasonalAdoptionInput) -> None:
    audit, epoch = value.audit, value.epoch_original
    if (
        epoch["id"] != str(value.epoch_id)
        or epoch["user_id"] != str(value.user_id)
        or epoch["status"] != "OPEN"
        or audit.user_id != value.user_id
        or audit.epoch_id != value.epoch_id
        or audit.status != "VALID"
        or audit.chain_status != "VALID"
        or audit.reference_status != "VALID"
        or audit.checkpoint_status not in {"VERIFIED", "NOT_REQUESTED"}
        or audit.actual_count != audit.expected_count
        or audit.actual_count != audit.verified_through_sequence
        or audit.actual_tail_id != audit.expected_tail_id
        or audit.actual_tail_hash != audit.expected_tail_hash
        or type(epoch["event_count"]) is not int
        or epoch["event_count"] != audit.expected_count
        or epoch["last_sequence"] != audit.verified_through_sequence
        or epoch["last_event_id"]
        != (str(audit.expected_tail_id) if audit.expected_tail_id is not None else None)
        or epoch["last_event_hash"] != audit.expected_tail_hash
        or audit.errors
        or audit.errors_truncated
    ):
        raise ValueError("ENDED_ADOPTION_COMPLETE_CURRENT_OPEN_AUDIT_NOT_VERIFIED")


def _proof(**values: Any) -> EndedSeasonalAdoptionProof:
    # Hash the complete proof, including null unknown values and all original inputs.
    proof = EndedSeasonalAdoptionProof(**values, proof_hash="0" * 64)
    return proof.model_copy(
        update={
            "proof_hash": configuration_hash(proof.model_dump(mode="json", exclude={"proof_hash"}))
        }
    )


def unknown_ended_seasonal_adoption(
    user_id: UUID, epoch_id: UUID, policy_id: UUID, now: datetime, reasons: list[str]
) -> EndedSeasonalAdoptionProof:
    return _proof(
        status="UNKNOWN",
        user_id=user_id,
        epoch_id=epoch_id,
        policy_id=policy_id,
        as_of=now,
        inputs=None,
        input_hash=None,
        registered_adoption_evidence_count=None,
        registered_adoption_evidence_ids=[],
        retained_command_ids=[],
        original=None,
        original_adopted_cents=None,
        protection_end=None,
        floor_released_on=None,
        current_floor_cents=None,
        reasons=sorted(set(reasons)),
    )


def derive_ended_seasonal_adoption(value: EndedSeasonalAdoptionInput) -> EndedSeasonalAdoptionProof:
    """Replay originals at their old clock, then prove expiry at the current local clock."""
    value = EndedSeasonalAdoptionInput.model_validate_json(value.model_dump_json())
    reasons: list[str] = []
    originals: list[SeasonalAdoptionOriginal] = []
    original: SeasonalAdoptionOriginal | None = None
    floor: Literal[0] | None = None
    status: Literal["VERIFIED_ENDED", "NO_ORIGINAL_ADOPTION", "UNKNOWN"] = "UNKNOWN"
    try:
        if len(value.model_dump_json().encode("utf-8")) > MAX_PROOF_BYTES:
            raise ValueError("ENDED_ADOPTION_COMPLETE_INPUT_CAPACITY_EXCEEDED")
        _audit(value)
        ids = value.registered_adoption_evidence_ids
        if (
            len(ids) != value.registered_adoption_evidence_count
            or len(set(ids)) != len(ids)
            or len(ids) != len(value.records)
            or set(ids) != {UUID(row.adoption_evidence_original["id"]) for row in value.records}
        ):
            raise ValueError("ENDED_ADOPTION_REGISTERED_ALL_OWNER_ROW_DENOMINATOR_INCOMPLETE")
        policy = value.current_policy_original
        if (
            policy["policy_id"] != str(value.policy_id)
            or policy["epoch_id"] != str(value.epoch_id)
            or policy["template_name"] != "SeasonalReservePolicy"
        ):
            raise ValueError("ENDED_ADOPTION_CURRENT_POLICY_OWNER_EPOCH_OR_TYPE_DIFFERS")
        originals = [_record(row, value.user_id) for row in value.records]
        if any(row.recorded_at > value.as_of for row in originals):
            raise ValueError("ENDED_ADOPTION_ORIGINAL_COMMAND_IN_FUTURE")
        retained = [row for row in originals if row.epoch_id == value.epoch_id]
        if len({row.command_id for row in retained}) != len(retained):
            raise ValueError("ENDED_ADOPTION_ORIGINAL_COMMAND_DUPLICATE")
        for index, left in enumerate(retained):
            for right in retained[index + 1 :]:
                if (
                    left.scope.official_start <= right.scope.official_end
                    and right.scope.official_start <= left.scope.official_end
                ):
                    raise ValueError("ENDED_ADOPTION_REGISTERED_WINDOWS_OVERLAP")
        selected = [row for row in retained if row.policy_id == value.policy_id]
        if not selected:
            status = "NO_ORIGINAL_ADOPTION"
        elif len(selected) != 1:
            raise ValueError("ENDED_ADOPTION_NONUNIQUE_ORIGINAL_POLICY_ADOPTION")
        else:
            original = selected[0]
            _same_policy(original, policy, value.as_of)
            day = value.as_of.astimezone(ZoneInfo(value.timezone)).date()
            if day <= original.scope.protection_end:
                raise ValueError("ENDED_ADOPTION_END_LOCAL_DAY_NOT_PASSED")
            status, floor = "VERIFIED_ENDED", 0
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        reasons.append(str(error))
    return _proof(
        status=status,
        user_id=value.user_id,
        epoch_id=value.epoch_id,
        policy_id=value.policy_id,
        as_of=value.as_of,
        inputs=value,
        input_hash=configuration_hash(value.model_dump(mode="json")),
        registered_adoption_evidence_count=value.registered_adoption_evidence_count,
        registered_adoption_evidence_ids=value.registered_adoption_evidence_ids,
        retained_command_ids=sorted(
            [row.command_id for row in originals if row.epoch_id == value.epoch_id], key=str
        ),
        original=original,
        original_adopted_cents=(
            original.scope.adopted_adjustment_cents if original is not None else None
        ),
        protection_end=original.scope.protection_end if original is not None else None,
        floor_released_on=(
            original.scope.protection_end + timedelta(days=1) if original is not None else None
        ),
        current_floor_cents=floor,
        reasons=sorted(set(reasons)),
    )


def verify_ended_seasonal_adoption(
    source: FullProtectionPolicySource,
    proof: EndedSeasonalAdoptionProof,
    as_of: datetime,
    timezone: str,
    *,
    expected_user_id: UUID | None = None,
) -> bool:
    """Opt-in new projection branch. Old v3 must never reinterpret this wrapper."""
    try:
        proof = EndedSeasonalAdoptionProof.model_validate_json(proof.model_dump_json())
        if proof.inputs is None or proof.status != "VERIFIED_ENDED":
            return False
        rebuilt = derive_ended_seasonal_adoption(proof.inputs)
        if rebuilt != proof or proof.original is None:
            return False
        version = proof.inputs.current_policy_original["current_version"]
        policy = proof.inputs.current_policy_original
        return (
            proof.as_of == as_of
            and proof.inputs.timezone == timezone
            and (expected_user_id is None or proof.user_id == expected_user_id)
            and source.policy_id == proof.policy_id
            and source.version_id == proof.original.scope.version_id
            and source.version_number == version["version_number"]
            and source.content_hash == version["content_hash"]
            and source.configuration == version["configuration"]
            and source.confirmation == version["confirmation"]
            and source.template_name == "SeasonalReservePolicy"
            and source.confirmed_at == datetime.fromisoformat(version["confirmed_at"])
            and source.valid_from == datetime.fromisoformat(version["valid_from"])
            and source.valid_until
            == (datetime.fromisoformat(version["valid_until"]) if version["valid_until"] else None)
            and source.effective_status == policy["effective_status"]
            and source.planning_confirmation_valid == policy["planning_confirmation_valid"]
            and source.references_current
            and {UUID(row) for row in version["evidence_ids"]} <= set(source.evidence_ids)
        )
    except (ValueError, TypeError, KeyError, OverflowError):
        return False
