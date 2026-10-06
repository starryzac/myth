"""A received maturity triggers a new current-scope decision, never a rollover grant."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from app.domain.asset_allocation_types import AssetAllocationResult
from app.domain.boundary_types import BoundaryModel
from app.domain.full_asset_allocation import FullAssetPlanningResult
from app.domain.policy_configuration import UUIDReference, configuration_hash
from pydantic import Field, StrictBool, StrictInt, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ReplanState = Literal[
    "ORIGINAL_RETURN_NOT_RECEIVED",
    "UNKNOWN",
    "BLOCKED",
    "RETAIN_CASH",
    "PREPARE_CURRENT_INTENT",
    "PLANNING_ONLY",
]


class MaturityReplanningRequest(BoundaryModel):
    maturity_action_id: UUIDReference
    current_asset_policy_id: UUIDReference
    expected_epoch_id: UUIDReference


class OriginalMaturityEvent(BoundaryModel):
    action_id: UUID
    position_id: UUID
    original_product_id: UUID
    original_policy_version_id: UUID | None
    goal_id: UUID | None
    destination_account_id: UUID | None
    original_action_status: str
    bank_operation_id: UUID | None
    original_bank_status: str | None
    original_receipt_id: UUID | None
    principal_cents: Annotated[StrictInt, Field(gt=0)]
    settled_at: datetime | None
    proof_status: Literal["VERIFIED", "NOT_RECEIVED", "UNKNOWN", "INVALID"]
    service_receipt_verified: StrictBool
    receipt_is_current_authority: Literal[False] = False
    economic_verified: Literal[False] = False
    absence_is_final: Literal[False] = False
    originals_hash: Digest
    originals: dict[str, Any]
    issues: list[str]

    @model_validator(mode="after")
    def complete_received_original(self) -> "OriginalMaturityEvent":
        if configuration_hash(self.originals) != self.originals_hash:
            raise ValueError("Complete captured maturity originals differ from their binding hash")
        if (self.proof_status == "VERIFIED") != self.service_receipt_verified:
            raise ValueError("An unverified event cannot claim a verified receipt")
        if self.service_receipt_verified and (
            self.bank_operation_id is None
            or self.original_receipt_id is None
            or self.original_bank_status != "SETTLED"
            or self.original_action_status not in {"SUCCEEDED", "RECONCILED"}
            or self.settled_at is None
            or self.destination_account_id is None
        ):
            raise ValueError(
                "Received maturity needs the complete original bank/application receipt"
            )
        return self


class CurrentMaturityPolicy(BoundaryModel):
    policy_id: UUID
    version_id: UUID
    kind: Literal["MVP", "FULL"]
    configuration_hash: Digest
    scope: Literal["general_idle_funds", "goal"]
    goal_id: UUID | None
    effective_status: str
    current_confirmation_verified: StrictBool
    current_bank_authority_verified: StrictBool
    original_configuration: dict[str, Any]

    @model_validator(mode="after")
    def exact_current_configuration(self) -> "CurrentMaturityPolicy":
        if configuration_hash(self.original_configuration) != self.configuration_hash:
            raise ValueError("Current configuration differs from its original hash")
        if self.scope != ("goal" if self.goal_id is not None else "general_idle_funds"):
            raise ValueError("Current scope must retain the actual goal identity")
        if (
            self.original_configuration.get("type") != "asset_authorization"
            or self.original_configuration.get("scope") != self.scope
            or self.original_configuration.get("goal_id")
            != (str(self.goal_id) if self.goal_id is not None else None)
        ):
            raise ValueError("Current original configuration must match this precise policy scope")
        if self.kind == "FULL" and self.current_bank_authority_verified:
            raise ValueError("A FULL planning confirmation is not a current bank grant")
        return self


class CurrentPurchaseIntent(BoundaryModel):
    endpoint: Literal["/api/v1/actions/prepare"] = "/api/v1/actions/prepare"
    request: dict[str, Any]
    request_hash: Digest
    current_policy_version_id: UUID
    reviewed_product_id: UUID
    reviewed_product_version_number: Annotated[StrictInt, Field(gt=0)]
    reviewed_terms_digest: Digest
    current_selection_hash: Digest
    binding_hash: Digest
    submit_intent_only: Literal[True] = True
    prepare_recomputes_and_may_differ: Literal[True] = True
    must_review_new_prepared_effect: Literal[True] = True
    bank_authority: Literal[False] = False


class MaturityDecision(BoundaryModel):
    algorithm_version: Literal["current-maturity-scope-replanning-v1"] = (
        "current-maturity-scope-replanning-v1"
    )
    state: ReplanState
    received_principal_cents: StrictInt | None
    candidate: CurrentPurchaseIntent | None
    reasons: list[str]
    input_hash: Digest
    bank_authority: Literal[False] = False
    executes_funds: Literal[False] = False
    enqueues_action: Literal[False] = False
    event_is_exclusive_funding_reservation: Literal[False] = False
    automatic_rollover: Literal[False] = False
    future_income_added_cents: Literal[0] = 0


def decide_maturity_replanning(
    *,
    epoch_id: UUID,
    event: OriginalMaturityEvent,
    policy: CurrentMaturityPolicy,
    sources_verified: bool,
    source_hash: str,
    legacy_plan: AssetAllocationResult | None,
    full_plan: FullAssetPlanningResult | None,
) -> MaturityDecision:
    """Do not take cash, products or authority from the historical purchase/receipt."""
    inputs = {
        "epoch_id": str(epoch_id),
        "event": event.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "sources_verified": sources_verified,
        "source_hash": source_hash,
        "legacy_plan": legacy_plan.model_dump(mode="json") if legacy_plan else None,
        "full_plan": full_plan.model_dump(mode="json") if full_plan else None,
    }
    input_hash = configuration_hash(inputs)
    reasons = list(event.issues)
    candidate = None
    state: ReplanState
    received = event.principal_cents if event.proof_status == "VERIFIED" else None
    if event.proof_status == "NOT_RECEIVED":
        state = "ORIGINAL_RETURN_NOT_RECEIVED"
    elif event.proof_status != "VERIFIED" or not event.service_receipt_verified:
        state = "UNKNOWN"
        received = None
        reasons.append("ORIGINAL_MATURITY_RECEIPT_NOT_VERIFIED")
    elif event.goal_id != policy.goal_id or policy.scope != (
        "goal" if event.goal_id is not None else "general_idle_funds"
    ):
        state = "BLOCKED"
        reasons.append("ORIGINAL_GOAL_OWNERSHIP_SCOPE_MISMATCH")
    elif not policy.current_confirmation_verified or (
        policy.kind == "MVP" and not policy.current_bank_authority_verified
    ):
        state = "BLOCKED"
        reasons.append("CURRENT_POLICY_NOT_AUTHORIZED_NOW")
    elif not sources_verified:
        state = "UNKNOWN"
        reasons.append("CURRENT_FINANCIAL_AUDIT_OR_CATALOGUE_NOT_VERIFIED")
    elif policy.kind == "FULL":
        if full_plan is None or (
            full_plan.policy_id != policy.policy_id
            or full_plan.policy_version_id != policy.version_id
            or full_plan.scope != policy.scope
            or full_plan.goal_id != policy.goal_id
        ):
            state = "UNKNOWN"
            reasons.append("CURRENT_FULL_PLANNING_RESULT_MISSING_OR_STALE")
        elif full_plan.status == "INACTIVE_POLICY":
            state = "BLOCKED"
            reasons.extend(full_plan.reasons)
        elif full_plan.status == "NO_PURCHASE":
            state = "RETAIN_CASH"
            reasons.extend(full_plan.reasons)
        elif full_plan.status == "OPTIMAL":
            state = "PLANNING_ONLY"
            reasons.append("FULL_PLANNING_HAS_NO_CURRENT_BANK_CONSUMER")
        else:
            state = "UNKNOWN"
            reasons.extend(full_plan.reasons)
    elif legacy_plan is None or (
        legacy_plan.policy_id != policy.policy_id
        or legacy_plan.policy_version_id != policy.version_id
        or legacy_plan.scope != policy.scope
        or legacy_plan.goal_id != policy.goal_id
    ):
        state = "UNKNOWN"
        reasons.append("CURRENT_MVP_PLANNING_RESULT_MISSING_OR_STALE")
    elif legacy_plan.status == "INACTIVE_POLICY":
        state = "BLOCKED"
        reasons.extend(legacy_plan.reasons)
    elif legacy_plan.status != "READY":
        state = "UNKNOWN"
        reasons.extend(legacy_plan.reasons)
    elif legacy_plan.selected_product_id is None or not legacy_plan.suggested_cents:
        state = "RETAIN_CASH"
        reasons.extend(legacy_plan.reasons)
    else:
        selected = next(
            (
                item
                for item in legacy_plan.candidates
                if item.product_id == legacy_plan.selected_product_id
                and item.status == "FEASIBLE"
                and item.exit_plan is not None
            ),
            None,
        )
        if selected is None or selected.exit_plan is None:
            state = "UNKNOWN"
            reasons.append("CURRENT_SELECTED_PRODUCT_OR_EXIT_NOT_VERIFIED")
        else:
            body = {
                "idempotency_key": "maturity-replan:"
                + configuration_hash(
                    {
                        "event_id": str(event.bank_operation_id),
                        "policy_id": str(policy.policy_id),
                        "policy_version_id": str(policy.version_id),
                        "epoch_id": str(epoch_id),
                    }
                ),
                "intent": {"kind": "purchase_asset", "policy_id": str(policy.policy_id)},
            }
            candidate = CurrentPurchaseIntent(
                request=body,
                request_hash=configuration_hash(body),
                current_policy_version_id=policy.version_id,
                reviewed_product_id=selected.product_id,
                reviewed_product_version_number=selected.version_number,
                reviewed_terms_digest=selected.exit_plan.terms_digest,
                current_selection_hash=legacy_plan.selection_hash,
                binding_hash=input_hash,
            )
            state = "PREPARE_CURRENT_INTENT"
            reasons.append("PREPARE_REQUIRES_FRESH_CURRENT_SOURCE_AND_EFFECT_REVIEW")
    return MaturityDecision(
        state=state,
        received_principal_cents=received,
        candidate=candidate,
        reasons=list(dict.fromkeys(reasons)),
        input_hash=input_hash,
    )
