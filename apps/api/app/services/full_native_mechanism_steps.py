"""Private DEVELOPMENT mechanism consumption through the original single-action pipeline.

The caller registers exact RULE locators outside the authored Case. Local cookies are
verified anew before every write; source/authority/bank checks remain in the producer.
"""

import json
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Annotated, Any

from app.db.audit_guard import audit_command_guard
from app.domain.boundary_types import BoundaryModel
from app.domain.full_asset_execution import FullAssetPrepareRequest, Key
from app.domain.full_experiment_asset_execution import FullExperimentAssetRequest
from app.domain.full_experiment_asset_selection import RegisteredFullMechanismRule
from app.domain.full_experiment_cases import original_json
from app.domain.local_actor_session_types import require_local_user
from app.domain.policy_configuration import UUIDReference
from app.services.action_contracts import ConfirmActionRequest, IntentModel
from app.services.execution import confirm_action, execute_action
from app.services.full_experiment_asset_execution import (
    FullExperimentAssetLookup,
    lookup_full_experiment_asset_execution,
    prepare_full_experiment_asset_execution,
)
from app.services.full_native_steps import FullNativeSteps
from app.services.local_actor_sessions import (
    COOKIE_NAME,
    LocalAuthenticationError,
    verify_local_actor_session,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.scenario_runner import _fault
from pydantic import Field, StrictStr
from sqlalchemy import text
from sqlalchemy.orm import Session

KINDS = frozenset(
    {
        "FULL_MECHANISM_PREPARE",
        "FULL_MECHANISM_LOOKUP",
        "FULL_MECHANISM_CONFIRM",
        "FULL_MECHANISM_EXECUTE",
    }
)
CAPTURE_PROTOCOL = "full-native-private-mechanism-call-v1"


class MechanismPrepare(BoundaryModel):
    rule_id: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]
    body: FullAssetPrepareRequest


class MechanismLookup(BoundaryModel):
    idempotency_key: Key


class MechanismConfirm(MechanismLookup):
    action_id: UUIDReference
    body: ConfirmActionRequest


class MechanismExecute(MechanismLookup):
    action_id: UUIDReference
    body: BoundaryModel  # The original execute request is empty, not caller financial input.


MODELS: dict[str, type[BoundaryModel]] = {
    "FULL_MECHANISM_PREPARE": MechanismPrepare,
    "FULL_MECHANISM_LOOKUP": MechanismLookup,
    "FULL_MECHANISM_CONFIRM": MechanismConfirm,
    "FULL_MECHANISM_EXECUTE": MechanismExecute,
}


def supported_fault(kind: str, fault: str) -> bool:
    return kind in KINDS and (
        fault == "NONE"
        or kind == "FULL_MECHANISM_EXECUTE"
        and fault in {"DROP_BANK_RESPONSE", "FAIL_APPLICATION_PROJECTION"}
    )


def parse_inputs(kind: str, inputs: dict[str, Any]) -> BoundaryModel:
    if kind not in MODELS:
        raise ValueError("FULL_PRIVATE_MECHANISM_KIND_NOT_IMPLEMENTED")
    return MODELS[kind].model_validate_json(json.dumps(inputs, allow_nan=False))


def apply_service_capture(capture: dict[str, Any], row: dict[str, Any]) -> None:
    """Retain original serialized service result/error without inventing an HTTP status."""
    raw = capture.get("original_return_text")
    value = capture.get("result") if capture.get("outcome") == "RETURNED" else capture.get("error")
    if (
        capture.get("protocol") != CAPTURE_PROTOCOL
        or capture.get("channel") != "PRIVATE_PRODUCTION_SERVICE_CALL"
        or capture.get("outcome") not in {"RETURNED", "RAISED"}
        or not isinstance(raw, str)
        or sha256(raw.encode("utf-8")).hexdigest() != capture.get("return_bytes_sha256")
        or original_json(raw.encode("utf-8")) != value
        or not isinstance(value, dict)
    ):
        raise ValueError("Private original service bytes/result/hash differ")
    row["private_service_response"] = capture
    row["result" if capture["outcome"] == "RETURNED" else "error"] = value


class FullNativeMechanismSteps:
    """No selection/authorization cache; exact-key recovery never reads a new RULE."""

    def __init__(
        self,
        native: FullNativeSteps,
        rules: Mapping[str, RegisteredFullMechanismRule],
    ):
        self.native = native
        self.rules = MappingProxyType(
            {
                key: RegisteredFullMechanismRule.model_validate_json(value.model_dump_json())
                for key, value in rules.items()
            }
        )

    def _user(self, now: datetime) -> dict[str, Any]:
        client = self.native._client
        token = client.cookies.get(COOKIE_NAME) if client is not None else None
        if token is None:
            raise PolicyLifecycleError(
                "LOCAL_USER_SESSION_REQUIRED", "Current USER login required", 401
            )
        try:
            principal = verify_local_actor_session(token, self.native.user_id, now)
            require_local_user(principal, self.native.user_id, now)
        except (LocalAuthenticationError, ValueError):
            raise PolicyLifecycleError(
                "LOCAL_USER_SESSION_REQUIRED", "Current signed USER session required", 401
            ) from None
        return principal.model_dump(mode="json")

    def _lookup(self, key: str, now: datetime) -> FullExperimentAssetLookup:
        with Session(self.native.engine) as session, session.begin():
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            return lookup_full_experiment_asset_execution(session, self.native.user_id, key, now)

    def dispatch(
        self, kind: str, inputs: dict[str, Any], now: datetime, *, fault: str = "NONE"
    ) -> dict[str, Any]:
        if not supported_fault(kind, fault):
            raise ValueError("FULL_PRIVATE_MECHANISM_FAULT_NOT_IMPLEMENTED")
        body = parse_inputs(kind, inputs)
        self.native._context(now)
        capture: dict[str, Any] = {
            "protocol": CAPTURE_PROTOCOL,
            "channel": "PRIVATE_PRODUCTION_SERVICE_CALL",
            "purpose": "DEVELOPMENT",
            "kind": kind,
            "fault": fault,
            "request": body.model_dump(mode="json"),
            "bank_authority_granted": False,
            "financial_effect_verified": False,
        }
        try:
            if kind != "FULL_MECHANISM_LOOKUP":
                capture["signed_user_session"] = self._user(now)
            if isinstance(body, MechanismPrepare):
                locator = self.rules.get(body.rule_id)
                if locator is None:
                    raise PolicyLifecycleError(
                        "FULL_MECHANISM_RULE_NOT_REGISTERED", "No server registered RULE", 409
                    )
                request = FullExperimentAssetRequest(
                    original_request=body.body, rule_original=locator
                )
                if body.body.expected_epoch_id != self.native.expected_epoch_id:
                    raise ValueError("Private request differs from the actual owned OPEN epoch")
                capture["private_request"] = request.model_dump(mode="json")
                result: IntentModel | BoundaryModel = prepare_full_experiment_asset_execution(
                    self.native.engine, self.native.user_id, request, now
                )
            else:
                assert isinstance(body, MechanismLookup)
                original = self._lookup(body.idempotency_key, now)
                capture["original_lookup"] = original.model_dump(mode="json")
                if isinstance(body, (MechanismConfirm, MechanismExecute)):
                    action = original.action
                    if (
                        original.status != "RECORDED"
                        or original.original_request is None
                        or original.original_request.original_request.expected_epoch_id
                        != self.native.expected_epoch_id
                        or action is None
                        or action.action_id != body.action_id
                        or action.user_id != self.native.user_id
                        or action.autonomy_level != "ASK_ONCE"
                    ):
                        raise PolicyLifecycleError(
                            "FULL_MECHANISM_ORIGINAL_IDENTITY_REQUIRED",
                            "Exact original key/action/epoch/ASK identity required",
                            409,
                        )
                    with audit_command_guard(self.native.engine, self.native.user_id):
                        if isinstance(body, MechanismConfirm):
                            result = confirm_action(
                                self.native.engine,
                                self.native.user_id,
                                body.action_id,
                                body.body,
                                now,
                            )
                        else:
                            with _fault(
                                fault,
                                "EXECUTE_ACTION",
                                self.native.engine,
                                self.native.user_id,
                                {"action_id": str(body.action_id)},
                            ):
                                result = execute_action(
                                    self.native.engine, self.native.user_id, body.action_id, now
                                )
                else:
                    result = original
            raw = result.model_dump_json()
            capture.update(outcome="RETURNED", result=json.loads(raw))
        except (PolicyLifecycleError, TimeoutError) as error:
            problem = (
                {
                    "code": error.code,
                    "message": error.message,
                    "status_code": error.status_code,
                    "exception_type": type(error).__name__,
                }
                if isinstance(error, PolicyLifecycleError)
                else {
                    "code": "SIMULATED_BANK_RESPONSE_LOST"
                    if fault == "DROP_BANK_RESPONSE"
                    and str(error) == "SIMULATED_BANK_RESPONSE_LOST"
                    else "ACTUAL_TIMEOUT",
                    "message": str(error),
                    "status_code": 409,
                    "exception_type": type(error).__name__,
                }
            )
            raw = json.dumps(problem, ensure_ascii=False, separators=(",", ":"))
            capture.update(outcome="RAISED", error=problem)
        capture.update(
            original_return_text=raw, return_bytes_sha256=sha256(raw.encode()).hexdigest()
        )
        return capture
