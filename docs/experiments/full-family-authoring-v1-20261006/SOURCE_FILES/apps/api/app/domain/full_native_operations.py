"""Fixed real FULL API operations for isolated development inputs, never arbitrary RPC."""

import re
from dataclasses import dataclass
from urllib.parse import urlencode
from typing import Any, Literal
from uuid import UUID

from app.domain.full_asset_allocation import FullAssetPlanOptions
from app.services.scenario_references import canonical


@dataclass(frozen=True)
class NativeOperation:
    method: Literal["GET", "POST"]
    path: str
    identities: tuple[str, ...] = ()
    key_identities: tuple[str, ...] = ()
    planning_query: bool = False


OPERATIONS: dict[str, NativeOperation] = {
    "LOCAL_USER_LOGIN": NativeOperation("POST", "/api/v1/local-actor/login"),
    "LOCAL_USER_READ": NativeOperation("GET", "/api/v1/local-actor/session"),
    "LOCAL_USER_LOGOUT": NativeOperation("POST", "/api/v1/local-actor/logout"),
    "FULL_POLICY_VALIDATE": NativeOperation("POST", "/api/v1/policy-templates/validate"),
    "FULL_POLICY_CONFIRM": NativeOperation("POST", "/api/v1/full-policies/confirm"),
    "FULL_POLICY_READ": NativeOperation("GET", "/api/v1/full-policies/{policy_id}", ("policy_id",)),
    "FULL_POLICY_CHANGE_PREVIEW": NativeOperation(
        "POST", "/api/v1/full-policies/{policy_id}/change-preview", ("policy_id",)
    ),
    "FULL_POLICY_CHANGE": NativeOperation(
        "POST", "/api/v1/full-policies/{policy_id}/change", ("policy_id",)
    ),
    "FULL_POLICY_SUSPEND": NativeOperation(
        "POST", "/api/v1/full-policies/{policy_id}/suspend", ("policy_id",)
    ),
    "FULL_POLICY_REVOKE": NativeOperation(
        "POST", "/api/v1/full-policies/{policy_id}/revoke", ("policy_id",)
    ),
    "FULL_POLICY_HISTORY_PREVIEW": NativeOperation(
        "POST", "/api/v1/full-policies/{policy_id}/financial-change-preview-history", ("policy_id",)
    ),
    "FULL_GOAL_PREVIEW": NativeOperation(
        "POST", "/api/v1/goals/{goal_id}/full-model/preview", ("goal_id",)
    ),
    "FULL_GOAL_CONFIRM": NativeOperation(
        "POST", "/api/v1/goals/{goal_id}/full-model/confirm", ("goal_id",)
    ),
    "FULL_GOAL_READ": NativeOperation("GET", "/api/v1/goals/{goal_id}/full-model", ("goal_id",)),
    "JOINT_GOAL_PLAN": NativeOperation("GET", "/api/v1/planning/full-current-goal-allocation"),
    "FULL_ANNUAL_READ": NativeOperation("GET", "/api/v1/planning/full-annual"),
    "CATALOG_REGISTER_CURRENT": NativeOperation(
        "POST", "/api/v1/catalog/products/register-current"
    ),
    "FULL_ASSET_PLAN": NativeOperation(
        "GET", "/api/v1/full-policies/{policy_id}/asset-allocation", ("policy_id",),
        planning_query=True,
    ),
    "FULL_GOAL_CONFLICTS": NativeOperation("GET", "/api/v1/planning/full-goal-conflicts"),
    "FULL_GOAL_REPAIR_PREVIEW": NativeOperation(
        "POST", "/api/v1/planning/full-goal-repairs/preview"
    ),
    "FULL_DYNAMIC_GOAL_PREPARE": NativeOperation(
        "POST", "/api/v1/dynamic-goal-actions/prepare"
    ),
    "FULL_RECOVERY_PLAN": NativeOperation(
        "GET", "/api/v1/full-policies/{policy_id}/recovery-planning", ("policy_id",)
    ),
    "FULL_RECOVERY_READ": NativeOperation(
        "GET", "/api/v1/actions/{action_id}", ("action_id",)
    ),
    "MATURITY_REPLAN": NativeOperation("POST", "/api/v1/full-maturity-replanning/preview"),
    "FULL_ASSET_PREVIEW": NativeOperation("POST", "/api/v1/full-asset-executions/preview"),
    "FULL_ASSET_PREPARE": NativeOperation("POST", "/api/v1/full-asset-executions/prepare"),
    "FULL_ASSET_CONFIRM": NativeOperation(
        "POST", "/api/v1/full-asset-executions/portfolios/{portfolio_id}/confirm", ("portfolio_id",)
    ),
    "FULL_ASSET_EXECUTE": NativeOperation(
        "POST",
        "/api/v1/full-asset-executions/portfolios/{portfolio_id}/execute-next",
        ("portfolio_id",),
    ),
    "FULL_ASSET_READ": NativeOperation(
        "GET", "/api/v1/full-asset-executions/portfolios/{portfolio_id}", ("portfolio_id",)
    ),
    "PAYMENT_PREVIEW": NativeOperation("POST", "/api/v1/full-payment-relations/preview"),
    "PAYMENT_START": NativeOperation("POST", "/api/v1/full-payment-relations/start"),
    "PAYMENT_CONFIRM": NativeOperation(
        "POST",
        "/api/v1/full-payment-relations/starts/{start_command_id}/confirm",
        ("start_command_id",),
    ),
    "PAYMENT_PREPARE": NativeOperation(
        "POST",
        "/api/v1/full-payment-relations/authorizations/{authorization_id}/prepare",
        ("authorization_id",),
    ),
    "PAYMENT_ACTION_CONFIRM": NativeOperation(
        "POST", "/api/v1/full-payment-relations/actions/{action_id}/confirm", ("action_id",)
    ),
    "PAYMENT_EXECUTE": NativeOperation(
        "POST", "/api/v1/full-payment-relations/actions/{action_id}/execute", ("action_id",)
    ),
    "PAYMENT_READ": NativeOperation(
        "GET", "/api/v1/full-payment-relations/actions/{action_id}", ("action_id",)
    ),
    "PAYMENT_COMMAND_READ": NativeOperation(
        "GET", "/api/v1/full-payment-relations/commands/{epoch_id}/by-key/{key}",
        ("epoch_id",), ("key",),
    ),
    "QUESTION_START": NativeOperation("POST", "/api/v1/finite-planning/sessions"),
    "QUESTION_ANSWER": NativeOperation(
        "POST", "/api/v1/finite-planning/sessions/{session_id}/answers", ("session_id",)
    ),
    "QUESTION_CLOSE": NativeOperation(
        "POST", "/api/v1/finite-planning/sessions/{session_id}/close", ("session_id",)
    ),
    "QUESTION_READ": NativeOperation(
        "GET", "/api/v1/finite-planning/sessions/{session_id}", ("session_id",)
    ),
    "QUESTION_REFRESH": NativeOperation(
        "POST", "/api/v1/finite-planning/sessions/{session_id}/refresh", ("session_id",)
    ),
    "SEASONAL_PREVIEW": NativeOperation(
        "POST", "/api/v1/seasonal-reserve-adoptions/{policy_id}/preview", ("policy_id",)
    ),
    "SEASONAL_CONFIRM": NativeOperation(
        "POST", "/api/v1/seasonal-reserve-adoptions/{policy_id}/confirm", ("policy_id",)
    ),
    "SEASONAL_READ": NativeOperation(
        "GET", "/api/v1/seasonal-reserve-adoptions/{policy_id}", ("policy_id",)
    ),
    "FULL_RECOVERY_PREVIEW": NativeOperation("POST", "/api/v1/full-recovery-actions/preview"),
    "FULL_RECOVERY_PREPARE": NativeOperation("POST", "/api/v1/full-recovery-actions/prepare"),
    "FULL_RECOVERY_CONFIRM": NativeOperation(
        "POST", "/api/v1/full-recovery-actions/actions/{action_id}/confirm", ("action_id",)
    ),
    "FULL_RECOVERY_EXECUTE": NativeOperation(
        "POST", "/api/v1/full-recovery-actions/actions/{action_id}/execute", ("action_id",)
    ),
}


@dataclass(frozen=True)
class NativeRequest:
    method: Literal["GET", "POST"]
    path: str
    body: dict[str, Any] | None
    server_login: bool = False


def native_request(kind: str, inputs: dict[str, Any], *, fault: str = "NONE") -> NativeRequest:
    """Resolve only route identities. The actual API validates its complete original DTO."""
    if fault != "NONE":
        raise ValueError("FULL_NATIVE_FAULT_PAIR_NOT_IMPLEMENTED")
    if kind not in OPERATIONS:
        raise ValueError("FULL_NATIVE_OPERATION_NOT_IMPLEMENTED")
    spec = OPERATIONS[kind]
    has_body = spec.method == "POST" and kind != "LOCAL_USER_LOGIN"
    expected = (
        set(spec.identities) | set(spec.key_identities)
        | ({"body"} if has_body else set())
        | ({"planning_constraints"} if spec.planning_query else set())
    )
    if set(inputs) != expected or (has_body and not isinstance(inputs["body"], dict)):
        raise ValueError("FULL_NATIVE_INPUT_KEYS_NOT_EXACT")
    canonical(inputs)
    identities: dict[str, str] = {}
    for key in spec.identities:
        raw = inputs[key]
        if not isinstance(raw, str) or re.fullmatch(r"[0-9a-f-]{36}", raw) is None:
            raise ValueError("FULL_NATIVE_IDENTITY_NOT_CANONICAL_UUID")
        if str(UUID(raw)) != raw:
            raise ValueError("FULL_NATIVE_IDENTITY_NOT_CANONICAL_UUID")
        identities[key] = raw
    for key in spec.key_identities:
        raw = inputs[key]
        if not isinstance(raw, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,139}", raw) is None:
            raise ValueError("FULL_NATIVE_KEY_NOT_SAFE_ORIGINAL_IDENTITY")
        identities[key] = raw
    body = inputs["body"] if has_body else None
    path = spec.path.format(**identities)
    if spec.planning_query:
        # Closed original planning DTO; it can only constrain the pool, never grant authority.
        import json

        options = FullAssetPlanOptions.model_validate_json(
            json.dumps(inputs["planning_constraints"], allow_nan=False)
        )
        query = {
            "planning_" + key: value
            for key, value in options.model_dump(mode="json", exclude_none=True).items()
        }
        path += "?" + urlencode(query)
    return NativeRequest(
        spec.method, path, body, kind == "LOCAL_USER_LOGIN"
    )
