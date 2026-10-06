"""Explicit whole matured principal: conditional proof, never a bank result."""

import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5

from app.domain.asset_allocation_types import FixedPrincipalTerms
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_recovery_planning import (
    FullRecoveryCandidate,
    FullRecoveryPlanningInput,
    _candidate,
    _linked_configuration,
    _project_net,
)
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import MoneyCents, UUIDReference, configuration_hash
from pydantic import Field, StrictBool, StrictStr, field_validator

ALGORITHM: Literal["full-maturity-user-execution-v1"] = "full-maturity-user-execution-v1"
MARKER = "full_maturity_execution"
GUARDS_VERSION = "full-maturity-user-guards-v1"
CONSENT_SOURCE = "FULL_MATURITY_USER_CONSENT"
KEY_PREFIX = "maturity:user:"
VALIDATION_INFO_KEY = "full_maturity_current_validation"
Hash = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[StrictStr, Field(min_length=1, max_length=120)]


class FullMaturityRequest(BoundaryModel):
    policy_id: UUIDReference
    expected_version_id: UUIDReference
    expected_epoch_id: UUIDReference
    position_id: UUIDReference
    idempotency_key: Key

    @field_validator("idempotency_key")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("An exact nonblank original key is required")
        return value


class FullMaturityConfirmation(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_command_hash: Hash
    accepted: StrictBool

    @field_validator("accepted")
    @classmethod
    def accepted_exactly(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit USER acceptance of this entire command is required")
        return value


class FullMaturityExecuteRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_command_hash: Hash


class MaturityContractCommand(BoundaryModel):
    user_id: UUID
    position_id: UUID
    position_account_id: UUID
    product_id: UUID
    goal_id: UUID | None
    original_policy_version_id: UUID
    destination_account_id: UUID
    principal_cents: Annotated[MoneyCents, Field(gt=0)]
    requested_at: datetime
    available_at: datetime
    expires_at: datetime
    kind: Literal["MATURE"] = "MATURE"


class FullMaturityInput(BoundaryModel):
    """Only the trusted snapshot adapter constructs this complete original input."""

    user_id: UUID
    epoch_id: UUID
    request: FullMaturityRequest
    as_of: datetime
    current_effective_status: str
    current_reference_validation: str
    planning: FullRecoveryPlanningInput
    protection: FullProtectionProjectionInput
    current_mvp_authorized: StrictBool
    independent_bank_principal_cents: MoneyCents
    independent_bank_head: dict[str, Any]
    source_evidence_ids: list[UUID]
    source_issues: list[tuple[str, str]]
    source_originals: list[dict[str, Any]]
    full_original: dict[str, Any]
    current_mvp_original: dict[str, Any]
    position_original: dict[str, Any]
    catalogue_original: dict[str, Any]
    original_full_projection: dict[str, Any]


class FullMaturityProof(BoundaryModel):
    protocol: Literal["full-maturity-user-execution-v1"] = ALGORITHM
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    bank_receipt_proven: Literal[False] = False
    projected_cash_is_current_cash: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    position_id: UUID
    as_of: datetime
    status: Literal["VERIFIED_SCOPE", "BLOCKED", "UNKNOWN"]
    reasons: list[str]
    command: MaturityContractCommand | None
    candidate_denominator: int
    input_hash: Hash
    command_hash: Hash | None
    conditional_protection_hash: Hash | None
    remaining_negative_checkpoints: int | None
    proof_hash: Hash


class FullMaturityConsent(BoundaryModel):
    protocol: Literal["full-maturity-user-execution-v1"] = ALGORITHM
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    action_id: UUID
    reviewed_command_hash: Hash
    original_confirmation: FullMaturityConfirmation
    principal_at_confirmation: LocalActorPrincipal
    confirmed_at: datetime


def maturity_bank_key(key: str) -> str:
    if type(key) is not str or not 1 <= len(key) <= 120 or not key.strip():
        raise ValueError("An exact nonblank original key is required")
    return KEY_PREFIX + configuration_hash({"key": key})


def maturity_command_hash(command: MaturityContractCommand, request: FullMaturityRequest) -> str:
    return configuration_hash(
        {
            "protocol": ALGORITHM,
            "request": request.model_dump(mode="json"),
            "bank_request": command.model_dump(mode="json"),
        }
    )


def explicit_mature_ask_eligible(candidate: FullRecoveryCandidate) -> bool:
    """An AUTO-redemption flag cannot veto an independently proven USER maturity.

    The legacy recovery planner remains unchanged. Only its single automatic
    product flag is irrelevant to this separate ASK contract. Every other
    original scope, availability, fee/loss and permission rejection survives.
    """
    if candidate.lossless_eligible and candidate.decision == "ASK_ONCE":
        return True
    return (
        candidate.decision == "BLOCKED"
        and candidate.liquidity_rank == 2
        and candidate.within_full_planning_limits
        and candidate.original_quote is not None
        and candidate.original_quote.kind == "MATURE"
        and candidate.fee_cents == 0
        and candidate.independent_loss_cents == 0
        and candidate.net_cents == candidate.principal_cents
        and candidate.reasons == ["ORIGINAL_PRODUCT_REDEMPTION_PERMISSION_MISSING"]
    )


def derive_maturity_proof(
    supplied: FullMaturityInput, original_command: MaturityContractCommand | None = None
) -> FullMaturityProof:
    data = FullMaturityInput.model_validate(supplied.model_dump())
    now, body, plan = data.as_of, data.request, data.planning
    blocked: list[str] = []
    unknown: list[str] = []
    command = None
    protection_hash = None
    remaining = None
    if (
        (body.policy_id, body.expected_version_id, body.expected_epoch_id)
        != (plan.policy_id, plan.policy_version_id, data.epoch_id)
        or plan.user_id != data.user_id
        or plan.snapshot.as_of != now
        or data.protection.snapshot != plan.snapshot
        or data.protection.positions != plan.positions
        or data.protection.boundary_versions != plan.boundary_versions
        or data.protection.boundary_products != plan.boundary_products
        or data.current_effective_status not in {"ACTIVE", "CONFIRMED"}
        or data.current_reference_validation != "CURRENT"
        or not plan.planning_confirmation_valid
        or not max(plan.confirmed_at, plan.valid_from) <= now
        or (plan.valid_until is not None and now >= plan.valid_until)
        or not data.current_mvp_authorized
    ):
        blocked.append("CURRENT_FULL_AND_MVP_CONFIRMED_SCOPE_REQUIRED")
    # This is precisely an independent-bank-proven but unprojected single mature
    # position. Its raw issue and original snapshot remain frozen in data.
    allowed = ("UNRECONCILED_POSITION_AVAILABILITY", str(body.position_id))
    if any(issue != allowed for issue in data.source_issues):
        unknown.append("OTHER_FINANCIAL_SOURCE_UNKNOWN")
    if any((row.code, row.entity_id) != allowed for row in plan.snapshot.source_issues):
        unknown.append("OTHER_SNAPSHOT_SOURCE_UNKNOWN")
    if not data.source_evidence_ids or len(set(data.source_evidence_ids)) != len(
        data.source_evidence_ids
    ):
        unknown.append("COMPLETE_UNIQUE_ORIGINAL_SOURCE_DENOMINATOR_REQUIRED")
    holding = [row for row in plan.holdings if row.original.position_id == body.position_id]
    positions = [row for row in plan.positions if row.position_id == body.position_id]
    if len(holding) != 1 or len(positions) != 1:
        unknown.append("ONE_POSITION_IN_COMPLETE_HOLDINGS_REQUIRED")
    else:
        held, position = holding[0], positions[0]
        raw = held.original
        try:
            fixed = FixedPrincipalTerms.model_validate(raw.product.maturity_rule)
            maturity = raw.purchased_at + timedelta(days=fixed.term_days)
            available = maturity + timedelta(days=fixed.settlement_delay_days)
            # No issued/derived redemption price substitutes for this original
            # fixed return contract; no client principal or assumed yield.
            if (
                held.maturity_at != maturity
                or fixed.term_days < raw.product.lock_days
                or fixed.settlement_delay_days != raw.product.redemption_delay_days
                or now < available
                or position.status not in {"HELD", "MATURED"}
                or raw.reserved_principal_cents
                or raw.original_authorization is None
                or data.independent_bank_principal_cents != position.principal_cents
                or position.principal_cents <= 0
            ):
                raise ValueError("Complete matured whole principal is not proven")
            # Reuse the original eligibility rules, without treating its derived
            # quote as a bank observation or requiring T+delay after maturity.
            quote = raw.quote
            if (
                quote is None
                or quote.kind != "MATURE"
                or any(value != 0 for value in (quote.fee_cents, quote.loss_cents))
                or quote.net_cents != position.principal_cents
            ):
                raise ValueError("An existing conflicting price cannot be replaced")
            linked = _linked_configuration(plan)
            selected = _candidate(plan, held, linked, None)
            if not explicit_mature_ask_eligible(selected):
                raise ValueError(",".join(selected.reasons))
            source = plan.linked_asset_policy
            if (
                not source.confirmation_valid
                or source.confirmed_at is None
                or source.valid_from is None
                or max(source.confirmed_at, source.valid_from) > now
                or (source.valid_until is not None and now >= source.valid_until)
            ):
                raise ValueError("Current linked asset permission is not confirmed")
            requested = original_command.requested_at if original_command else now
            expiry = (
                original_command.expires_at if original_command else now + timedelta(minutes=15)
            )
            command = MaturityContractCommand(
                user_id=data.user_id,
                position_id=body.position_id,
                position_account_id=raw.account_id,
                product_id=raw.product.product_id,
                goal_id=raw.goal_id,
                original_policy_version_id=raw.original_authorization.version_id,
                destination_account_id=raw.destination_account_id,
                principal_cents=position.principal_cents,
                requested_at=requested,
                available_at=requested,
                expires_at=expiry,
            )
            if (
                not available <= requested <= now < expiry
                or expiry - requested != timedelta(minutes=15)
                or (original_command is not None and command != original_command)
            ):
                raise ValueError("Original whole maturity command cannot change or extend")
            # Baseline explicitly withholds the sole unsettled principal. The
            # conditional result moves it to its SAME cash/Goal ownership only.
            base_snapshot = plan.snapshot.model_copy(update={"source_issues": []})
            base_positions = [
                row.model_copy(update={"principal_available_at": None})
                if row.position_id == body.position_id
                else row
                for row in plan.positions
            ]
            base_input = data.protection.model_copy(
                update={"snapshot": base_snapshot, "positions": base_positions}
            )
            baseline = project_full_protection(base_input)
            projected, projected_positions = _project_net(base_snapshot, base_positions, held, now)
            conditional = project_full_protection(
                base_input.model_copy(
                    update={"snapshot": projected, "positions": projected_positions}
                )
            )
            if (
                not baseline.full_obligations_complete_within_registered_current_scope
                or not conditional.full_obligations_complete_within_registered_current_scope
                or baseline.full_annual_projection is None
                or conditional.full_annual_projection is None
            ):
                unknown.append("COMPLETE_CURRENT_FULL_PROTECTION_NOT_PROVEN")
            else:
                before = baseline.full_annual_projection.calculation_trace
                after = conditional.full_annual_projection.calculation_trace
                if (
                    len(before) != 1098
                    or len(after) != 1098
                    or any(
                        (a.day, a.phase, a.date) != (b.day, b.phase, b.date)
                        or b.margin_cents < a.margin_cents
                        for a, b in zip(before, after, strict=True)
                    )
                ):
                    blocked.append("CURRENT_FULL_PROTECTION_WOULD_WORSEN")
                protection_hash = configuration_hash(conditional.model_dump(mode="json"))
                remaining = sum(point.margin_cents < 0 for point in after)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            blocked.append("EXACT_CURRENT_MATURE_CONTRACT_REQUIRED:" + str(error))
    reasons = list(dict.fromkeys(unknown + blocked))
    status: Literal["VERIFIED_SCOPE", "BLOCKED", "UNKNOWN"] = (
        "UNKNOWN" if unknown else ("BLOCKED" if blocked else "VERIFIED_SCOPE")
    )
    payload = dict(
        user_id=data.user_id,
        epoch_id=data.epoch_id,
        position_id=body.position_id,
        as_of=now,
        status=status,
        reasons=reasons,
        command=command,
        candidate_denominator=len(plan.holdings),
        input_hash=configuration_hash(data.model_dump(mode="json")),
        command_hash=maturity_command_hash(command, body) if command else None,
        conditional_protection_hash=protection_hash,
        remaining_negative_checkpoints=remaining,
    )
    unsigned = FullMaturityProof.model_validate({**payload, "proof_hash": "0" * 64})
    return unsigned.model_copy(
        update={
            "proof_hash": configuration_hash(
                unsigned.model_dump(mode="json", exclude={"proof_hash"})
            )
        }
    )


def verify_maturity_consent(
    consent: FullMaturityConsent,
    action_id: UUID,
    user_id: UUID,
    epoch_id: UUID,
    command_hash: str,
    now: datetime,
    *,
    current: bool,
) -> None:
    require_local_user(consent.principal_at_confirmation, user_id, consent.confirmed_at)
    if (
        (consent.user_id, consent.epoch_id, consent.action_id, consent.reviewed_command_hash)
        != (user_id, epoch_id, action_id, command_hash)
        or consent.original_confirmation.expected_epoch_id != epoch_id
        or consent.original_confirmation.reviewed_command_hash != command_hash
        or consent.confirmed_at > now
        or (current and now >= consent.principal_at_confirmation.expires_at)
    ):
        raise ValueError("Original signed USER one-command consent does not match")


def maturity_action_payload(
    data: FullMaturityInput, proof: FullMaturityProof, action_id: UUID
) -> dict[str, Any]:
    if proof.status != "VERIFIED_SCOPE" or proof.command is None or proof.command_hash is None:
        raise ValueError("An entire verified original maturity command is required")
    held = next(
        row
        for row in data.planning.holdings
        if row.original.position_id == data.request.position_id
    )
    return {
        "bank_request": proof.command.model_dump(mode="json"),
        "contract_terms_digest": held.original.product.terms_digest,
        "contract_evidence_ids": [str(key) for key in data.source_evidence_ids],
        MARKER: {
            "protocol": ALGORITHM,
            "user_id": str(data.user_id),
            "epoch_id": str(data.epoch_id),
            "action_id": str(action_id),
            "request": data.request.model_dump(mode="json"),
            "client_request_hash": configuration_hash(data.request.model_dump(mode="json")),
            "command_hash": proof.command_hash,
            "original_proof_hash": proof.proof_hash,
        },
    }


def verify_frozen_maturity_trace(trace: DecisionTrace) -> None:
    """Exact new historical input verification; no Session/fresh reads/recursive audit."""
    verify_trace(trace)
    if trace.algorithm_versions.get(MARKER) != ALGORITHM or trace.action_id is None:
        raise ValueError("Exact maturity algorithm and original action are required")
    try:
        action_id = trace.action_id
        payload = trace.inputs["action_request"]
        marker = payload[MARKER]
        if set(payload) != {
            "bank_request",
            "contract_terms_digest",
            "contract_evidence_ids",
            MARKER,
        } or set(marker) != {
            "protocol",
            "user_id",
            "epoch_id",
            "action_id",
            "request",
            "client_request_hash",
            "command_hash",
            "original_proof_hash",
        }:
            raise ValueError("Closed original maturity payload schema required")
        body = FullMaturityRequest.model_validate_json(json.dumps(marker["request"]))
        command = MaturityContractCommand.model_validate_json(json.dumps(payload["bank_request"]))
        if (
            command.user_id != trace.user_id
            or command.position_id != body.position_id
            or marker["protocol"] != ALGORITHM
            or marker["user_id"] != str(trace.user_id)
            or marker["epoch_id"] != str(body.expected_epoch_id)
            or marker["action_id"] != str(action_id)
            or marker["client_request_hash"] != configuration_hash(body.model_dump(mode="json"))
            or marker["command_hash"] != maturity_command_hash(command, body)
            or action_id != uuid5(body.expected_epoch_id, maturity_bank_key(body.idempotency_key))
        ):
            raise ValueError("Frozen whole request, action, epoch or exact key was rebound")
        if trace.phase == "PREPARE":
            if set(trace.inputs) != {"maturity_input", "action_request"}:
                raise ValueError("Complete original PREPARE input schema required")
            data = FullMaturityInput.model_validate_json(json.dumps(trace.inputs["maturity_input"]))
            proof = derive_maturity_proof(data)
            if (
                data.request != body
                or data.user_id != trace.user_id
                or data.as_of != trace.as_of
                or trace.run_id != uuid5(action_id, "maturity-user-prepare")
                or trace.parent_run_id is not None
                or payload != maturity_action_payload(data, proof, action_id)
                or trace.outcome != {"maturity_proof": proof.model_dump(mode="json")}
                or {row.id for row in trace.sources} != set(data.source_evidence_ids)
                or [row.model_dump(mode="json") for row in trace.sources] != data.source_originals
            ):
                raise ValueError("Frozen whole maturity proof/source denominator cannot reproduce")
            return
        if trace.phase == "CONFIRM":
            if set(trace.inputs) != {"confirmation", "action_request", "bank_key"}:
                raise ValueError("Complete original CONFIRM input schema required")
            consent = FullMaturityConsent.model_validate_json(
                json.dumps(trace.outcome["maturity_user_consent"])
            )
            confirmation = FullMaturityConfirmation.model_validate_json(
                json.dumps(trace.inputs["confirmation"])
            )
            verify_maturity_consent(
                consent,
                action_id,
                trace.user_id,
                body.expected_epoch_id,
                marker["command_hash"],
                trace.as_of,
                current=True,
            )
            if (
                len(trace.sources) != 1
                or trace.run_id != uuid5(action_id, "maturity-user-confirmation-trace")
                or trace.parent_run_id != uuid5(action_id, "maturity-user-prepare")
                or trace.inputs["bank_key"] != maturity_bank_key(body.idempotency_key)
                or consent.original_confirmation != confirmation
                or consent.confirmed_at != trace.as_of
                or trace.outcome != {"maturity_user_consent": consent.model_dump(mode="json")}
                or not command.requested_at <= trace.as_of < command.expires_at
            ):
                raise ValueError(
                    "Original USER confirmation does not bind the entire exact command"
                )
            source = trace.sources[0]
            if (
                source.id != uuid5(action_id, "maturity-user-consent")
                or source.source_type != CONSENT_SOURCE
                or source.source_ref != str(action_id)
                or source.evidence_level != "USER_CONFIRMED_ACTION"
                or source.content != consent.model_dump(mode="json")
                or source.status_at_decision != "VALID"
                or source.content_integrity != "VERIFIED"
                or source.observed_at != trace.as_of
                or source.valid_from != trace.as_of
                or source.valid_to != command.expires_at
            ):
                raise ValueError("Original USER evidence is missing, changed or replaced")
            return
        if trace.phase == "BANK_ACCEPT":
            validation = trace.inputs["validation_inputs"]["full_maturity_validation"]
            if set(validation) != {
                "action_id",
                "inputs",
                "proof",
                "original_consent",
                "consent_source",
            }:
                raise ValueError("Closed actual bank validation schema required")
            data = FullMaturityInput.model_validate_json(json.dumps(validation["inputs"]))
            proof = derive_maturity_proof(data, command)
            consent = FullMaturityConsent.model_validate_json(
                json.dumps(validation["original_consent"])
            )
            verify_maturity_consent(
                consent,
                action_id,
                trace.user_id,
                body.expected_epoch_id,
                marker["command_hash"],
                trace.as_of,
                current=True,
            )
            sources = {str(row.id): row.model_dump(mode="json") for row in trace.sources}
            if (
                validation["action_id"] != str(action_id)
                or data.request != body
                or data.user_id != trace.user_id
                or data.as_of != trace.as_of
                or proof.status != "VERIFIED_SCOPE"
                or proof.command != command
                or validation["proof"] != proof.model_dump(mode="json")
                or trace.inputs["bank_request"] != command.model_dump(mode="json")
                or trace.parent_run_id != uuid5(action_id, "maturity-user-prepare")
                or trace.run_id != uuid5(action_id, "legacy-bank-accept-decision")
                or trace.outcome.get("autonomy_level") != "ASK_ONCE"
                or trace.outcome.get("new_authority") is not False
                or trace.outcome.get("settlement_kind") != "ORIGINAL_CONTRACT"
                or trace.outcome.get("bank_validation_status") != "READY"
                or len(data.source_originals) != len(data.source_evidence_ids)
                or {row["id"] for row in data.source_originals}
                != {str(key) for key in data.source_evidence_ids}
                or any(sources.get(row["id"]) != row for row in data.source_originals)
                or sources.get(validation["consent_source"]["id"]) != validation["consent_source"]
            ):
                raise ValueError(
                    "Actual fresh bank Full/USER/protection validation cannot reproduce"
                )
            source = validation["consent_source"]
            if (
                source["id"] != str(uuid5(action_id, "maturity-user-consent"))
                or source["content"] != consent.model_dump(mode="json")
                or source["source_type"] != CONSENT_SOURCE
                or source["evidence_level"] != "USER_CONFIRMED_ACTION"
                or source["status_at_decision"] != "VALID"
                or source["content_integrity"] != "VERIFIED"
                or source["source_ref"] != str(action_id)
                or source["observed_at"] != consent.confirmed_at.isoformat().replace("+00:00", "Z")
                or source["valid_from"] != source["observed_at"]
                or source["valid_to"] != command.expires_at.isoformat().replace("+00:00", "Z")
            ):
                raise ValueError("Original confirmed USER source missing from fresh bank proof")
            return
        raise ValueError("Unsupported new maturity phase")
    except (KeyError, TypeError, StopIteration) as error:
        raise ValueError("Whole maturity historical original input is missing") from error
