"""Explicit adoption of one original seasonal suggestion; no payment or bank grant."""

from __future__ import annotations

import json
from datetime import date, datetime
from fractions import Fraction
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_policy_configuration import SeasonalReservePolicy, validate_full_configuration
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.pattern_suggestions import (
    HistoryProof,
    SeasonalParameters,
    SeasonalSpend,
    SuggestionSource,
    public_calendar,
    seasonal_suggestion,
)
from app.domain.policy_configuration import MoneyCents, Reference, UUIDReference, configuration_hash
from pydantic import Field, StrictBool, model_validator

if TYPE_CHECKING:
    from app.domain.full_protection_projection import FullProtectionPolicySource

PROTOCOL: Literal["full-seasonal-adoption-v1"] = "full-seasonal-adoption-v1"
SOURCE = "FULL_SEASONAL_ADOPTION"
REFERENCE_KIND = "VERIFIED_SEASONAL_ADOPTION"
NAMESPACE = UUID("caaf871c-09d1-44c4-baf1-8b0d8f9fef83")
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]
MAX_ADOPTIONS = 200


class SeasonalAdoptionPreviewRequest(BoundaryModel):
    expected_version_id: UUIDReference
    window_id: Reference


class SeasonalAdoptionConfirmRequest(SeasonalAdoptionPreviewRequest):
    expected_epoch_id: UUIDReference
    reviewed_hash: Hash
    accepted: StrictBool
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    idempotency_key: Key

    @model_validator(mode="after")
    def explicit(self) -> Self:
        if self.accepted is not True or self.reason != self.reason.strip():
            raise ValueError("Explicit acceptance and a nonblank exact reason are required")
        return self


class SeasonalAdoptionScope(BoundaryModel):
    protocol: Literal["full-seasonal-adoption-v1"] = PROTOCOL
    user_id: UUID
    epoch_id: UUID
    policy_id: UUID
    version_id: UUID
    configuration_hash: Hash
    evaluated_at: datetime
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    window_id: str
    holiday_code: str
    official_start: date
    official_end: date
    protection_start: date
    protection_end: date
    adopted_adjustment_cents: MoneyCents
    required_adjustment_cents: MoneyCents
    cap_limited: StrictBool
    full_policy_original: dict[str, Any]
    suggestion_original: dict[str, Any]
    source_evidence_originals: list[dict[str, Any]]
    parameters: SeasonalParameters
    future_income_in_current_cash_cents: Literal[0] = 0
    bank_authority: Literal[False] = False
    payment_or_settlement_proven: Literal[False] = False


def _policy(scope: SeasonalAdoptionScope) -> SeasonalReservePolicy:
    row = scope.full_policy_original
    version = row["current_version"]
    config = SeasonalReservePolicy.model_validate(
        validate_full_configuration("SeasonalReservePolicy", version["configuration"])
    )
    if (
        row["policy_id"] != str(scope.policy_id)
        or row["epoch_id"] != str(scope.epoch_id)
        or row["template_name"] != "SeasonalReservePolicy"
        or row["effective_status"] not in {"CONFIRMED", "ACTIVE"}
        or row["planning_confirmation_valid"] is not True
        or row["reference_validation"] != "CURRENT"
        or version["version_id"] != str(scope.version_id)
        or version["content_hash"] != scope.configuration_hash
        or configuration_hash(version["configuration"]) != scope.configuration_hash
        or version["confirmation_evidence_status"] != "CURRENT_EVIDENCE_MATCHED"
        or version["confirmation"].get("accepted") is not True
        or config.holiday_code != scope.holiday_code
        or config.window.end != scope.official_end
        or not scope.official_start <= config.window.start <= scope.protection_start
        or scope.protection_end != scope.official_end
        or config.lookback_days != scope.parameters.lookback_days
        or config.minimum_historical_windows != scope.parameters.minimum_historical_windows
        or Fraction(str(config.quantile)) != Fraction(scope.parameters.quantile_bps, 10000)
        or config.essential_categories != scope.parameters.essential_categories
        or config.adjustment_cap_cents != scope.parameters.adjustment_cap_cents
    ):
        raise ValueError("Original current FULL confirmation/window is not exact")
    return config


def validate_seasonal_scope(scope: SeasonalAdoptionScope) -> None:
    """Recheck the frozen original source shape; service also verifies bank/audit live."""
    scope = SeasonalAdoptionScope.model_validate(scope.model_dump())
    config = _policy(scope)
    original = scope.suggestion_original
    suggestion = original["suggestion"]
    target = suggestion["target"]
    proof = original["history_proof"]
    today = scope.evaluated_at.astimezone(ZoneInfo(scope.timezone)).date()
    if (
        original["user_id"] != str(scope.user_id)
        or original["as_of"] != scope.evaluated_at.isoformat().replace("+00:00", "Z")
        or suggestion["status"] != "READY"
        or original["source_issues"]
        or proof["verified"] is not True
        or proof["reason_codes"]
        or suggestion["reason_codes"]
        or suggestion["window_count"] < scope.parameters.minimum_historical_windows
        or target["window_id"] != scope.window_id
        or target["holiday_code"] != scope.holiday_code
        or target["start"] != scope.official_start.isoformat()
        or target["end"] != scope.official_end.isoformat()
        or scope.protection_start != max(today, scope.official_start)
        or not scope.protection_start <= scope.protection_end
        or suggestion["effective_window_start"] != scope.protection_start.isoformat()
        or suggestion["effective_window_end"] != scope.protection_end.isoformat()
        or suggestion["proposed_adjustment_cents"] != scope.adopted_adjustment_cents
        or suggestion["required_adjustment_cents"] != scope.required_adjustment_cents
        or type(suggestion["proposed_adjustment_cents"]) is not int
        or scope.adopted_adjustment_cents > config.adjustment_cap_cents
        or scope.adopted_adjustment_cents > scope.required_adjustment_cents
        or scope.cap_limited != (scope.adopted_adjustment_cents < scope.required_adjustment_cents)
        or original["hard_protection_changed"] is not False
        or original["bank_authority"] is not False
    ):
        raise ValueError("Only a complete original READY suggestion may be explicitly adopted")
    evidence = {row["id"]: row for row in scope.source_evidence_originals}
    expected = set(original["source_evidence_ids"]) | set(
        scope.full_policy_original["current_version"]["evidence_ids"]
    )
    if len(evidence) != len(scope.source_evidence_originals) or set(evidence) != expected:
        raise ValueError("The full original evidence denominator is required")
    for row in evidence.values():
        if (
            row["user_id"] != str(scope.user_id)
            or row["status"] != "VALID"
            or configuration_hash(row["content"]) != row["content_hash"]
            or datetime.fromisoformat(row["observed_at"]) > scope.evaluated_at
            or datetime.fromisoformat(row["valid_from"]) > scope.evaluated_at
            or (
                row["valid_to"] is not None
                and datetime.fromisoformat(row["valid_to"]) <= scope.evaluated_at
            )
        ):
            raise ValueError("Original evidence owner/hash/time is not current")
    for source in original["sources"]:
        source_row = evidence.get(source["evidence_id"])
        if (
            source_row is None
            or source_row["content_hash"] != source["evidence_hash"]
            or source_row["source_type"] != source["evidence_source_type"]
            or source_row["source_ref"] != source["evidence_source_ref"]
            or datetime.fromisoformat(source_row["observed_at"])
            != datetime.fromisoformat(source["evidence_observed_at"])
        ):
            raise ValueError("Original suggestion source is absent from the denominator")
    source_keys = [(row["fact_type"], row["fact_id"]) for row in original["sources"]]
    if len(set(source_keys)) != len(source_keys):
        raise ValueError("The original suggestion source denominator has duplicate identities")
    certificates = [
        row
        for row in evidence.values()
        if row["source_type"] == "SIMULATED_TRANSACTION_HISTORY_COVERAGE"
    ]
    if len(certificates) != 1:
        raise ValueError("The full original history certificate is not unique")
    certificate = certificates[0]
    coverage = certificate["content"]
    if (
        certificate["evidence_level"] != "BANK_CONFIRMED"
        or coverage["simulation"] is not True
        or coverage["protocol"] != "transaction-history-coverage-v1"
        or coverage["user_id"] != str(scope.user_id)
        or coverage["timezone"] != scope.timezone
        or coverage["period_start"] != proof["period_start"]
        or coverage["period_end"] != proof["period_end"]
        or coverage["scope_account_ids"] != sorted(proof["account_ids"])
        or len(set(coverage["scope_account_ids"])) != len(coverage["scope_account_ids"])
        or sorted(row["account_id"] for row in coverage["accounts"])
        != coverage["scope_account_ids"]
        or any(
            type(row["transaction_count"]) is not int or row["transaction_count"] < 0
            for row in coverage["accounts"]
        )
    ):
        raise ValueError("Original complete coverage owner/period/accounts/counts changed")
    bank_rows = [
        row for row in evidence.values() if row["source_type"] == "SIMULATED_BANK_TRANSACTION"
    ]
    for account in coverage["accounts"]:
        if account["transaction_count"] != sum(
            row["content"].get("account_id") == account["account_id"] for row in bank_rows
        ):
            raise ValueError("The actual captured bank-history evidence denominator is incomplete")
    version = scope.full_policy_original["current_version"]
    confirmation = version["confirmation"]
    confirmation_row = evidence.get(confirmation["confirmation_evidence_id"])
    if (
        confirmation["user_id"] != str(scope.user_id)
        or confirmation["epoch_id"] != str(scope.epoch_id)
        or confirmation["policy_id"] != str(scope.policy_id)
        or confirmation["version_id"] != str(scope.version_id)
        or confirmation["reviewed_hash"] != scope.configuration_hash
        or confirmation["bank_authority"] is not False
        or confirmation_row is None
        or confirmation_row["evidence_level"] != "USER_CONFIRMED_POLICY"
        or confirmation_row["source_type"] != "FULL_POLICY_CONFIRMATION"
        or confirmation_row["source_ref"] != str(scope.version_id)
        or confirmation_row["content"] != confirmation
        or datetime.fromisoformat(confirmation["confirmed_at"])
        != datetime.fromisoformat(version["confirmed_at"])
    ):
        raise ValueError("Original FULL confirmation evidence is not exact")
    if original["public_windows"] != [
        row.model_dump(mode="json") for row in public_calendar(today)
    ] or original["calendar_extraction_hash"] != configuration_hash(
        {"windows": original["public_windows"]}
    ):
        raise ValueError("The original server calendar source is not exact")
    spends = []
    for raw in original["sources"]:
        if raw["fact_type"] != "transactions":
            continue
        bank = evidence[raw["evidence_id"]]
        fact = raw["fact"]
        categories = [
            row
            for row in evidence.values()
            if row["source_type"] == "SIMULATED_USER_CATEGORY_CONFIRMATION"
            and row["content"].get("transaction_id") == raw["fact_id"]
        ]
        if (
            len(categories) != 1
            or categories[0]["evidence_level"] != "USER_DECLARED"
            or categories[0]["content"].get("confirmed") is not True
            or categories[0]["content"].get("simulation") is not True
            or bank["evidence_level"] != "BANK_CONFIRMED"
            or bank["content"].get("economic_role") != "CONSUMPTION"
            or bank["content"].get("simulation") is not True
            or fact["transaction_id"] != raw["fact_id"]
            or fact["direction"] != "DEBIT"
            or configuration_hash({key: bank["content"].get(key) for key in fact})
            != configuration_hash(fact)
        ):
            raise ValueError("Original classified consumption is not exact")
        category = categories[0]["content"]["category"]
        if category in scope.parameters.essential_categories:
            spends.append(
                SeasonalSpend(
                    transaction_id=UUID(raw["fact_id"]),
                    occurred_on=datetime.fromisoformat(fact["occurred_at"])
                    .astimezone(ZoneInfo(scope.timezone))
                    .date(),
                    amount_cents=fact["amount_cents"],
                    category=category,
                    sources=[SuggestionSource.model_validate_json(json.dumps(raw))],
                )
            )
    calculated = seasonal_suggestion(
        scope.parameters,
        today,
        HistoryProof.model_validate_json(json.dumps(proof)),
        spends,
        {},
    )
    if calculated.model_dump(mode="json") != suggestion:
        raise ValueError(
            "Adopted integer amount must recompute from the original historical sources"
        )


def seasonal_review_hash(scope: SeasonalAdoptionScope) -> str:
    validate_seasonal_scope(scope)
    value = scope.model_dump(mode="json")
    # Only the three exact read-clock-derived fields are removed. All fact times,
    # original periods, evidence, current state, amounts and classifications remain.
    value.pop("evaluated_at")
    value["suggestion_original"].pop("as_of")
    value["suggestion_original"].pop("source_digest")
    return configuration_hash(value)


def seasonal_source_hash(scope: SeasonalAdoptionScope) -> str:
    """Later days never reduce the fixed adoption. Compare complete historical sources."""
    validate_seasonal_scope(scope)
    suggestion = scope.suggestion_original
    comparisons = [
        {key: value for key, value in row.items() if key != "scaled_excess_cents"}
        for row in suggestion["suggestion"]["comparisons"]
    ]
    return configuration_hash(
        {
            "user_id": str(scope.user_id),
            "epoch_id": str(scope.epoch_id),
            "policy": scope.full_policy_original,
            "parameters": scope.parameters.model_dump(mode="json"),
            "window_id": scope.window_id,
            "official_start": scope.official_start.isoformat(),
            "official_end": scope.official_end.isoformat(),
            "calendar_extraction_hash": suggestion["calendar_extraction_hash"],
            "history_proof": suggestion["history_proof"],
            "comparisons": comparisons,
            "sources": suggestion["sources"],
            "evidence": scope.source_evidence_originals,
        }
    )


def seasonal_command_id(user_id: UUID, epoch_id: UUID, key: str) -> UUID:
    return uuid5(NAMESPACE, f"{user_id}:{epoch_id}:{key}")


class SeasonalAdoptionOriginal(BoundaryModel):
    protocol: Literal["full-seasonal-adoption-v1"] = PROTOCOL
    command_id: UUID
    user_id: UUID
    epoch_id: UUID
    policy_id: UUID
    idempotency_key: Key
    original_request: SeasonalAdoptionConfirmRequest
    request_hash: Hash
    reviewed_hash: Hash
    source_binding_hash: Hash
    principal_at_command: LocalActorPrincipal
    recorded_at: datetime
    scope: SeasonalAdoptionScope
    future_income_in_current_cash_cents: Literal[0] = 0
    bank_authority: Literal[False] = False
    financial_execution_performed: Literal[False] = False

    @model_validator(mode="after")
    def bound(self) -> Self:
        require_local_user(self.principal_at_command, self.user_id, self.recorded_at)
        body = self.original_request
        if (
            self.command_id
            != seasonal_command_id(self.user_id, self.epoch_id, self.idempotency_key)
            or self.scope.user_id != self.user_id
            or self.scope.epoch_id != self.epoch_id
            or self.scope.policy_id != self.policy_id
            or self.scope.evaluated_at != self.recorded_at
            or body.expected_epoch_id != self.epoch_id
            or body.expected_version_id != self.scope.version_id
            or body.window_id != self.scope.window_id
            or body.idempotency_key != self.idempotency_key
            or body.reviewed_hash != self.reviewed_hash
            or self.reviewed_hash != seasonal_review_hash(self.scope)
            or self.source_binding_hash != seasonal_source_hash(self.scope)
            or self.request_hash != seasonal_request_hash(self.user_id, self.policy_id, body)
        ):
            raise ValueError("Original USER command/body/review/period binding is not exact")
        return self


def seasonal_request_hash(
    user_id: UUID, policy_id: UUID, body: SeasonalAdoptionConfirmRequest
) -> str:
    return configuration_hash(
        {
            "user_id": str(user_id),
            "policy_id": str(policy_id),
            "request": body.model_dump(mode="json"),
        }
    )


def assert_no_seasonal_overlap(
    scope: SeasonalAdoptionScope, originals: list[SeasonalAdoptionOriginal]
) -> None:
    for original in originals:
        if original.user_id != scope.user_id or original.epoch_id != scope.epoch_id:
            raise ValueError("Overlap denominator has another owner or epoch")
        prior = original.scope
        if (
            prior.official_start <= scope.official_end
            and scope.official_start <= prior.official_end
        ):
            raise ValueError(
                "A registered period already has a fixed adoption; cannot double count"
            )


class SeasonalAdoptionProof(BoundaryModel):
    simulation: Literal[True] = True
    protocol: Literal["full-seasonal-adoption-v1"] = PROTOCOL
    status: Literal["VERIFIED", "ADVICE_ONLY", "UNKNOWN"]
    user_id: UUID
    epoch_id: UUID
    policy_id: UUID
    as_of: datetime
    original: SeasonalAdoptionOriginal | None = None
    evidence_id: UUID | None = None
    evidence_hash: Hash | None = None
    trace_hash: Hash | None = None
    current_scope: SeasonalAdoptionScope | None = None
    actual_adoption_count: int
    retained_command_ids: list[UUID]
    reasons: list[str]
    bank_authority: Literal[False] = False
    financial_execution_performed: Literal[False] = False


def verify_seasonal_adoption(
    source: FullProtectionPolicySource,
    proof: SeasonalAdoptionProof,
    as_of: datetime,
    timezone: str,
) -> bool:
    """New opt-in pure branch; absence/uncertainty is never a zero adopted amount."""
    try:
        proof = SeasonalAdoptionProof.model_validate_json(proof.model_dump_json())
        original, current = proof.original, proof.current_scope
        if original is None or current is None or proof.status != "VERIFIED":
            return False
        original = SeasonalAdoptionOriginal.model_validate_json(original.model_dump_json())
        validate_seasonal_scope(current)
        day = as_of.astimezone(ZoneInfo(timezone)).date()
        return (
            timezone == current.timezone
            and proof.as_of == as_of == current.evaluated_at
            and original.recorded_at <= as_of
            and proof.user_id == original.user_id == current.user_id
            and proof.epoch_id == original.epoch_id == current.epoch_id
            and proof.policy_id == original.policy_id == source.policy_id
            and original.command_id in proof.retained_command_ids
            and proof.actual_adoption_count == len(proof.retained_command_ids)
            and len(set(proof.retained_command_ids)) == proof.actual_adoption_count
            and proof.evidence_id == uuid5(original.command_id, "evidence")
            and proof.evidence_hash == configuration_hash(original.model_dump(mode="json"))
            and proof.trace_hash is not None
            and not proof.reasons
            and original.source_binding_hash == seasonal_source_hash(current)
            and source.template_name == "SeasonalReservePolicy"
            and source.version_id == original.scope.version_id == current.version_id
            and source.content_hash == original.scope.configuration_hash
            and source.configuration
            == current.full_policy_original["current_version"]["configuration"]
            and source.confirmation
            == current.full_policy_original["current_version"]["confirmation"]
            and source.confirmed_at
            == datetime.fromisoformat(
                current.full_policy_original["current_version"]["confirmed_at"]
            )
            and source.valid_from
            == datetime.fromisoformat(current.full_policy_original["current_version"]["valid_from"])
            and source.valid_until
            == (
                datetime.fromisoformat(
                    current.full_policy_original["current_version"]["valid_until"]
                )
                if current.full_policy_original["current_version"]["valid_until"] is not None
                else None
            )
            and {
                UUID(value)
                for value in current.full_policy_original["current_version"]["evidence_ids"]
            }
            <= set(source.evidence_ids)
            and source.planning_confirmation_valid
            and source.references_current
            and source.effective_status in {"ACTIVE", "CONFIRMED"}
            and day <= original.scope.protection_end
        )
    except (ValueError, TypeError, KeyError):
        return False


def original_from_json(value: dict[str, Any]) -> SeasonalAdoptionOriginal:
    return SeasonalAdoptionOriginal.model_validate_json(json.dumps(value))


def verify_frozen_seasonal_adoption_trace(trace: DecisionTrace) -> SeasonalAdoptionOriginal:
    """New exact algorithm replay entry; this cannot approve a present bank action."""
    verify_trace(trace)
    if trace.algorithm_versions != {"trace": "decision-trace-v1", "seasonal_adoption": PROTOCOL}:
        raise ValueError("Only the exact registered seasonal-adoption algorithm is supported")
    if set(trace.outcome) != {"seasonal_adoption_original"}:
        raise ValueError("The complete original adoption is required")
    original = original_from_json(trace.outcome["seasonal_adoption_original"])
    if (
        trace.run_id != original.command_id
        or trace.user_id != original.user_id
        or trace.as_of != original.recorded_at
        or trace.phase != "EVALUATION"
        or trace.action_id is not None
        or trace.parent_run_id is not None
        or trace.policies
        or trace.constraints
        or trace.candidates
        or trace.inputs
        != {
            "original_request": original.original_request.model_dump(mode="json"),
            "request_hash": original.request_hash,
            "reviewed_hash": original.reviewed_hash,
        }
    ):
        raise ValueError("The original command identity/body/time/source is not exact")
    expected = {UUID(row["id"]): row for row in original.scope.source_evidence_originals}
    proof_id = uuid5(original.command_id, "evidence")
    expected[proof_id] = {
        "user_id": str(original.user_id),
        "evidence_level": "USER_CONFIRMED_POLICY",
        "source_type": SOURCE,
        "source_ref": str(original.command_id),
        "status": "VALID",
        "content": original.model_dump(mode="json"),
        "content_hash": configuration_hash(original.model_dump(mode="json")),
        "observed_at": original.recorded_at.isoformat(),
        "valid_from": original.recorded_at.isoformat(),
        "valid_to": None,
    }
    if {row.id for row in trace.sources} != set(expected):
        raise ValueError("The original source denominator is incomplete")
    for source in trace.sources:
        row = expected[source.id]
        if (
            source.user_id != UUID(row["user_id"])
            or source.evidence_level != row["evidence_level"]
            or source.source_type != row["source_type"]
            or source.source_ref != row["source_ref"]
            or source.status_at_decision != row["status"]
            or source.content != row["content"]
            or source.content_hash != row["content_hash"]
            or source.observed_at != datetime.fromisoformat(row["observed_at"])
            or source.valid_from != datetime.fromisoformat(row["valid_from"])
            or source.valid_to
            != (datetime.fromisoformat(row["valid_to"]) if row["valid_to"] is not None else None)
            or source.content_integrity != "VERIFIED"
        ):
            raise ValueError("The original captured evidence metadata/content is not exact")
    return original
