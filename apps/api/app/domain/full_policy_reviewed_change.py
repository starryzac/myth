"""Closed reviewed-change observations; no evidence or financial authority is minted."""

import json
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel, BoundaryResult
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_policy_change_multi import PHASES, SourceKind
from app.domain.full_policy_configuration import validate_full_configuration
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import (
    UUIDReference,
    configuration_hash,
    validate_configuration,
)
from app.services.full_policy_change_multi import (
    MultiTemplatePreviewRequest,
    MultiTemplatePreviewResponse,
)
from pydantic import Field, StrictBool, field_validator, model_validator

ALGORITHM = "reviewed-multi-template-policy-change-v1"
MARKER = "reviewed_policy_change"
MAX_REVIEW_BYTES = 6 * 1024 * 1024
EXCLUDED_METADATA = frozenset(
    {
        "decision_runs",
        "decision_constraints",
        "audit_events",
        "audit_epochs",
        "audit_subject_snapshots",
        "audit_archive_snapshots",
    }
)
GLOBAL_TABLES = frozenset({"asset_products", "product_catalog_versions"})


class ReviewRequest(MultiTemplatePreviewRequest):
    idempotency_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")


class ConfirmReviewedChangeRequest(ReviewRequest):
    review_id: UUIDReference
    accepted: StrictBool
    reviewed_configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_review_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_scope: Literal[
        "MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS",
        "INDIVIDUAL_PRODUCT_CAPACITY",
        "WHOLE_POSITION_RECOVERY_CANDIDATES",
        "CURRENT_JOINT_GOAL_ALLOCATION",
    ]
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("accepted")
    @classmethod
    def explicit(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("EXPLICIT_USER_FINANCIAL_REVIEW_REQUIRED")
        return value

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("NONBLANK_CHANGE_REASON_REQUIRED")
        return value


class SourceBasis(BoundaryModel):
    protocol: Literal["registered-policy-change-source-basis-v1"] = (
        "registered-policy-change-source-basis-v1"
    )
    user_id: UUID
    epoch_id: UUID
    local_day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    registered_tables: list[str]
    excluded_metadata_tables: list[str]
    tables: dict[str, list[dict[str, Any]]]
    row_counts: dict[str, int]
    complete: Literal[True] = True

    @model_validator(mode="after")
    def denominators(self) -> "SourceBasis":
        if date.fromisoformat(self.local_day).isoformat() != self.local_day:
            raise ValueError("CANONICAL_LOCAL_DAY_REQUIRED")
        if (
            not self.registered_tables
            or self.registered_tables != sorted(set(self.registered_tables))
            or self.excluded_metadata_tables != sorted(set(self.excluded_metadata_tables))
            or not set(self.excluded_metadata_tables) <= EXCLUDED_METADATA
            or set(self.registered_tables) != set(self.tables) | set(self.excluded_metadata_tables)
            or set(self.tables) & EXCLUDED_METADATA
            or set(self.row_counts) != set(self.tables)
            or "users" not in self.tables
            or len(self.tables["users"]) != 1
            or self.tables["users"][0].get("id") != str(self.user_id)
        ):
            raise ValueError("COMPLETE_REGISTERED_SOURCE_TABLE_DENOMINATOR_REQUIRED")
        for name, rows in self.tables.items():
            ids = [str(row.get("id")) for row in rows]
            if len(rows) > 10000 or self.row_counts[name] != len(rows) or ids != sorted(set(ids)):
                raise ValueError("COMPLETE_SOURCE_ROW_DENOMINATOR_REQUIRED")
            for row in rows:
                if str(UUID(row["id"])) != row["id"]:
                    raise ValueError("CANONICAL_SOURCE_ROW_ID_REQUIRED")
                if name not in GLOBAL_TABLES | {"users"} and row.get("user_id") != str(
                    self.user_id
                ):
                    raise ValueError("SAME_OWNER_COMPLETE_SOURCE_ROWS_REQUIRED")
        configuration_hash(self.model_dump(mode="json"))
        return self


def coverage(preview: MultiTemplatePreviewResponse) -> tuple[list[str], list[str], bool]:
    impact = preview.financial_impact
    missing = sorted(
        set(
            preview.limitations
            + impact.limitations
            + [
                "FUTURE_ACTION_DIFFERENCE_NOT_PROVEN",
                "REVIEW_RECEIPT_IS_NOT_BANK_EXECUTION_GRANT",
            ]
        )
    )
    supported = (
        impact.status in {"PROJECTED", "PARTIAL"}
        and impact.before is not None
        and impact.after is not None
    )
    if impact.scope == "INDIVIDUAL_PRODUCT_CAPACITY":
        supported = supported and len(impact.product_capacities) == preview.source_counts.get(
            "asset_catalogue"
        )
        supported = supported and all(
            row.before_capacity_cents is not None and row.after_capacity_cents is not None
            for row in impact.product_capacities
        )
    elif impact.scope == "WHOLE_POSITION_RECOVERY_CANDIDATES":
        supported = supported and len(impact.recovery_candidates) == preview.source_counts.get(
            "recovery_holdings"
        )
        supported = supported and all(
            row.conditional_on_time_net_delta_cents is not None
            for row in impact.recovery_candidates
        )
    elif impact.scope == "CURRENT_JOINT_GOAL_ALLOCATION":
        supported = (
            supported
            and impact.goal_allocation_before is not None
            and impact.goal_allocation_after is not None
        )
        supported = supported and all(
            row.status != "UNKNOWN"
            for row in (impact.goal_allocation_before, impact.goal_allocation_after)
            if row is not None
        )
    elif impact.scope != "MVP_PROTECTION_WITH_UNCHANGED_FULL_BURDENS":
        supported = False
    if impact.status == "PARTIAL":
        missing.extend(impact.reasons)
    if not supported:
        missing.extend(impact.reasons or ["FINANCIAL_CONFIRMATION_SCOPE_NOT_PROVEN"])
    return [impact.scope] if supported else [], sorted(set(missing)), supported


def curve_value(curve: BoundaryResult | None) -> dict[str, Any] | None:
    if curve is None:
        return None
    if len(curve.calculation_trace) != 1098:
        raise ValueError("FULL_1098_REVIEW_MATRIX_REQUIRED")
    first = curve.calculation_trace[0].date
    for index, point in enumerate(curve.calculation_trace):
        if (
            point.day != index // 3
            or point.date != first + timedelta(days=index // 3)
            or point.phase != PHASES[index % 3]
            or any(
                type(value) is not int or value < 0
                for value in point.protected_cents_by_reason.values()
            )
            or point.margin_cents
            != point.cash_cents - sum(point.protected_cents_by_reason.values())
        ):
            raise ValueError("COMPLETE_ORDERED_REVIEW_MATRIX_REQUIRED")
    return curve.model_dump(mode="json", exclude={"boundary_hash", "algorithm_version"})


def impact_value(preview: MultiTemplatePreviewResponse, *, after: bool) -> dict[str, Any]:
    impact = preview.financial_impact
    result: dict[str, Any] = {
        "scope": impact.scope,
        "curve": curve_value(impact.after if after else impact.before),
        "owned_cash_delta": impact.current_owned_cash_delta_cents,
        "principal_delta": impact.current_position_principal_delta_cents,
        "future_action": impact.future_action_delta,
    }
    if impact.scope == "INDIVIDUAL_PRODUCT_CAPACITY":
        result["products"] = [
            {
                "id": str(row.product_id),
                "version": row.product_version,
                "terms": row.terms_digest,
                "capacity": row.after_capacity_cents if after else row.before_capacity_cents,
                "reasons": row.after_reasons if after else row.before_reasons,
            }
            for row in impact.product_capacities
        ]
    elif impact.scope == "WHOLE_POSITION_RECOVERY_CANDIDATES":
        result["recovery"] = [
            {"position_id": str(row.position_id), "value": row.after if after else row.before}
            for row in impact.recovery_candidates
        ]
    elif impact.scope == "CURRENT_JOINT_GOAL_ALLOCATION":
        allocation = impact.goal_allocation_after if after else impact.goal_allocation_before
        result["allocation"] = (
            allocation.model_dump(mode="json", exclude={"input_hash"}) if allocation else None
        )
    return result


class ReviewedChangeRecord(BoundaryModel):
    protocol: Literal["reviewed-policy-change-record-v1"] = "reviewed-policy-change-record-v1"
    review_id: UUID
    user_id: UUID
    epoch_id: UUID
    source_kind: SourceKind
    policy_id: UUID
    request: ReviewRequest
    captured_at: datetime
    expires_at: datetime
    actor: LocalActorPrincipal
    source_basis: SourceBasis
    source_basis_hash: str
    preview: MultiTemplatePreviewResponse
    covered_scopes: list[str]
    uncovered_items: list[str]
    confirmation_eligible: bool
    review_hash: str
    bank_authority: Literal[False] = False
    independent_financial_verification: Literal[False] = False


def review_digest(record: ReviewedChangeRecord) -> str:
    return configuration_hash(record.model_dump(mode="json", exclude={"review_hash"}))


def review_original_text(record: ReviewedChangeRecord) -> str:
    """Keep every original field without weakening the old trace scalar-money gate."""
    return json.dumps(
        record.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def read_review_original(inputs: dict[str, Any]) -> ReviewedChangeRecord:
    text = inputs["review_record_json"]
    if type(text) is not str or len(text.encode("utf-8")) > MAX_REVIEW_BYTES:
        raise ValueError("COMPLETE_BOUNDED_REVIEW_ORIGINAL_REQUIRED")
    record = ReviewedChangeRecord.model_validate_json(text)
    verify_record(record)
    if text != review_original_text(record) or inputs["review_record_hash"] != configuration_hash(
        record.model_dump(mode="json")
    ):
        raise ValueError("EXACT_REVIEW_ORIGINAL_TEXT_AND_HASH_REQUIRED")
    return record


def verify_record(record: ReviewedChangeRecord) -> None:
    preview = record.preview
    require_local_user(record.actor, record.user_id, record.captured_at)
    canonical = (
        validate_configuration(record.request.configuration)
        if record.source_kind == "MVP_POLICY"
        else validate_full_configuration(preview.template_name, record.request.configuration)
    )
    timezone = ZoneInfo(record.source_basis.tables["users"][0]["timezone"])
    if (
        (
            preview.user_id,
            preview.epoch_id,
            preview.source_kind,
            preview.policy_id,
            preview.expected_version_id,
            preview.as_of,
        )
        != (
            record.user_id,
            record.epoch_id,
            record.source_kind,
            record.policy_id,
            record.request.expected_version_id,
            record.captured_at,
        )
        or record.epoch_id != record.request.expected_epoch_id
        or (record.source_basis.user_id, record.source_basis.epoch_id)
        != (record.user_id, record.epoch_id)
        or record.source_basis_hash
        != configuration_hash(record.source_basis.model_dump(mode="json"))
        or record.review_hash != review_digest(record)
        or preview.candidate_configuration_hash != configuration_hash(preview.after_configuration)
        or preview.current_configuration_hash != configuration_hash(preview.before_configuration)
        or canonical != preview.after_configuration
        or preview.financial_impact.template_name != preview.template_name
        or record.source_basis.local_day
        != record.captured_at.astimezone(timezone).date().isoformat()
        or (record.covered_scopes, record.uncovered_items, record.confirmation_eligible)
        != coverage(preview)
        or not record.captured_at < record.expires_at <= record.actor.expires_at
        or (record.expires_at - record.captured_at).total_seconds() > 900
        or len(record.model_dump_json().encode()) > MAX_REVIEW_BYTES
    ):
        raise ValueError("REGISTERED_REVIEW_OR_SOURCE_BINDING_DIFFERS")
    SourceBasis.model_validate(record.source_basis.model_dump())
    if record.confirmation_eligible:
        impact_value(preview, after=False)
        impact_value(preview, after=True)
        for curve in (preview.financial_impact.before, preview.financial_impact.after):
            if (
                curve is not None
                and curve.calculation_trace[0].date.isoformat() != record.source_basis.local_day
            ):
                raise ValueError("REVIEW_MATRIX_LOCAL_DAY_DIFFERS")


def require_current_review(
    record: ReviewedChangeRecord,
    body: ConfirmReviewedChangeRequest,
    basis: SourceBasis,
    fresh: MultiTemplatePreviewResponse,
    now: datetime,
) -> None:
    verify_record(record)
    if (
        not record.captured_at <= now < record.expires_at
        or not record.confirmation_eligible
        or body.review_id != record.review_id
        or body.expected_epoch_id != record.epoch_id
        or body.expected_version_id != record.request.expected_version_id
        or body.reviewed_review_hash != record.review_hash
        or body.reviewed_configuration_hash != fresh.candidate_configuration_hash
        or body.reviewed_configuration_hash != record.preview.candidate_configuration_hash
        or body.reviewed_scope not in record.covered_scopes
        or (
            fresh.user_id,
            fresh.epoch_id,
            fresh.source_kind,
            fresh.policy_id,
            fresh.expected_version_id,
            fresh.as_of,
        )
        != (
            record.user_id,
            record.epoch_id,
            record.source_kind,
            record.policy_id,
            record.request.expected_version_id,
            now,
        )
        or fresh.after_configuration != record.preview.after_configuration
        or configuration_hash(basis.model_dump(mode="json")) != record.source_basis_hash
        or (fresh.source_counts, fresh.original_action_ids, fresh.original_position_ids)
        != (
            record.preview.source_counts,
            record.preview.original_action_ids,
            record.preview.original_position_ids,
        )
        or coverage(fresh) != coverage(record.preview)
        or impact_value(fresh, after=False) != impact_value(record.preview, after=False)
        or impact_value(fresh, after=True) != impact_value(record.preview, after=True)
    ):
        raise ValueError("STALE_OR_UNSUPPORTED_FINANCIAL_REVIEW")


def outer_request(
    user_id: UUID, kind: SourceKind, policy_id: UUID, body: ConfirmReviewedChangeRequest
) -> dict[str, Any]:
    return {
        "protocol": "reviewed-policy-change-command-v1",
        "user_id": str(user_id),
        "source_kind": kind,
        "policy_id": str(policy_id),
        "body": body.model_dump(mode="json"),
    }


def legacy_request(
    user_id: UUID,
    kind: SourceKind,
    policy_id: UUID,
    body: ConfirmReviewedChangeRequest,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Exact original lifecycle envelopes; the outer review hash is separate."""
    if kind == "MVP_POLICY":
        return {
            "user_id": str(user_id),
            "policy_id": str(policy_id),
            "expected_version_id": str(body.expected_version_id),
            "configuration": configuration,
            "reason": body.reason,
            "accepted": True,
        }
    return {
        "protocol": "full-policy-command-v1",
        "kind": "CHANGE",
        "user_id": str(user_id),
        "policy_id": str(policy_id),
        "body": {
            "expected_version_id": str(body.expected_version_id),
            "configuration": configuration,
            "accepted": True,
            "reviewed_hash": body.reviewed_configuration_hash,
            "reason": body.reason,
            "idempotency_key": body.idempotency_key,
        },
    }


def verify_frozen_reviewed_policy_change_trace(trace: DecisionTrace) -> None:
    """Verify only registered metadata binding, never independent bank/matrix success."""
    if trace.phase != "EVALUATION" or trace.algorithm_versions != {MARKER: ALGORITHM}:
        raise ValueError("EXACT_REVIEWED_POLICY_CHANGE_ALGORITHM_REQUIRED")
    phase = trace.inputs.get("review_phase")
    if phase == "REVIEW":
        if set(trace.inputs) != {"review_phase", "review_record_json", "review_record_hash"}:
            raise ValueError("CLOSED_REVIEW_TRACE_INPUT_REQUIRED")
        record = read_review_original(trace.inputs)
        expected = {
            "review_hash": record.review_hash,
            "confirmation_eligible": record.confirmation_eligible,
            "bank_authority": False,
        }
        if (
            trace.user_id != record.user_id
            or trace.run_id != record.review_id
            or trace.as_of != record.captured_at
            or trace.outcome != expected
        ):
            raise ValueError("REVIEW_TRACE_IDENTITY_DIFFERS")
    elif phase == "CONFIRM":
        raw = trace.inputs
        if set(raw) != {
            "review_phase",
            "user_id",
            "request",
            "actor",
            "outer_request",
            "outer_request_hash",
            "legacy_request",
            "legacy_request_hash",
            "lifecycle_receipt",
        }:
            raise ValueError("CLOSED_CONFIRM_TRACE_INPUT_REQUIRED")
        body = ConfirmReviewedChangeRequest.model_validate_json(json.dumps(raw["request"]))
        actor = LocalActorPrincipal.model_validate_json(json.dumps(raw["actor"]))
        require_local_user(actor, trace.user_id, trace.as_of)
        outer = raw["outer_request"]
        kind: SourceKind = outer["source_kind"]
        if kind not in {"MVP_POLICY", "FULL_POLICY"}:
            raise ValueError("EXACT_LEGACY_SOURCE_KIND_REQUIRED")
        policy_id = UUID(outer["policy_id"])
        configuration = (
            raw["legacy_request"].get("configuration")
            if kind == "MVP_POLICY"
            else raw["legacy_request"]["body"]["configuration"]
        )
        receipt = raw["lifecycle_receipt"]
        if (
            raw["user_id"] != str(trace.user_id)
            or trace.parent_run_id != body.review_id
            or outer != outer_request(trace.user_id, kind, policy_id, body)
            or raw["outer_request_hash"] != configuration_hash(raw["outer_request"])
            or configuration_hash(configuration) != body.reviewed_configuration_hash
            or raw["legacy_request"]
            != legacy_request(trace.user_id, kind, policy_id, body, configuration)
            or raw["legacy_request_hash"] != configuration_hash(raw["legacy_request"])
            or receipt.get("policy_id") != str(policy_id)
            or (
                kind == "FULL_POLICY"
                and (
                    receipt.get("epoch_id") != str(body.expected_epoch_id)
                    or receipt.get("configuration_hash") != body.reviewed_configuration_hash
                )
            )
            or (
                kind == "MVP_POLICY"
                and receipt.get("previous_version_id") != str(body.expected_version_id)
            )
            or trace.outcome
            != {
                "lifecycle_receipt": raw["lifecycle_receipt"],
                "bank_authority": False,
                "financial_readback_status": "NOT_READ_AFTER_COMMIT",
            }
        ):
            raise ValueError("CONFIRM_TRACE_ORIGINAL_BODY_AND_RECEIPT_DIFFERS")
    else:
        raise ValueError("REVIEW_TRACE_PHASE_NOT_SUPPORTED")
