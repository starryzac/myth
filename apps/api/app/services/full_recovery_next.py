"""One explicitly requested server-selected position via the unchanged v1 writers."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.db.audit_guard import audit_command_guard
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import execution_effect_hash
from app.domain.full_recovery_next import (
    PROTOCOL,
    FullRecoveryNextRequest,
    FullRecoveryNextSelection,
    derive_next_whole_selection,
    next_whole_v1_key,
)
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionResponse
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.full_recovery_execution import (
    FullRecoveryExecutionLookup,
    FullRecoveryExecutionPreview,
    _reader,
    lookup_full_recovery_execution,
    prepare_full_recovery_execution,
    preview_full_recovery_execution,
)
from app.services.full_recovery_planning import (
    FullRecoveryPlanningResponse,
    read_full_recovery_planning,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class FullRecoveryNextLookup(BoundaryModel):
    protocol: Literal["full-recovery-next-whole-v2"] = PROTOCOL
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    current_authority: Literal[False] = False
    not_found_is_final: Literal[False] = False
    automatically_advances: Literal[False] = False
    user_id: UUID
    idempotency_key: str
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    request_binding_kind: Literal["PROJECTION_OF_VERIFIED_V1_ORIGINAL_AND_EXACT_ROOT_KEY"] = (
        "PROJECTION_OF_VERIFIED_V1_ORIGINAL_AND_EXACT_ROOT_KEY"
    )
    original_v2_request_separately_recorded: Literal[False] = False
    bound_request: FullRecoveryNextRequest | None = None
    bound_request_hash: str | None = None
    original_v1_lookup: FullRecoveryExecutionLookup


class FullRecoveryNextPreview(BoundaryModel):
    protocol: Literal["full-recovery-next-whole-v2"] = PROTOCOL
    simulation: Literal[True] = True
    read_only: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    atomic_combination: Literal[False] = False
    automatically_advances: Literal[False] = False
    user_id: UUID
    as_of: datetime
    request: FullRecoveryNextRequest
    status: Literal[
        "READY_TO_PREPARE",
        "BLOCKED",
        "UNKNOWN",
        "NO_ELIGIBLE_NEXT_POSITION",
        "ORIGINAL_ACTION_RETAINED",
    ]
    planning: FullRecoveryPlanningResponse | None = None
    selection: FullRecoveryNextSelection | None = None
    original_v1_preview: FullRecoveryExecutionPreview | None = None
    existing: FullRecoveryNextLookup
    input_hash: str
    limitations: list[str]


LIMITATIONS = [
    "ORIGINAL_V1_ASK_USER_CONSENT_CURRENT_AUTHORITY_AND_FULL_PROTECTION_STILL_REQUIRED",
    "ONE_EXPLICIT_ROOT_KEY_BINDS_ONE_ORIGINAL_ACTION_NOT_AN_ATOMIC_MULTI_POSITION_GROUP",
    "FOLLOWING_POSITION_REQUIRES_EXPLICIT_FRESH_NEW_REQUEST_NO_AUTOMATIC_SUBMISSION",
    "NO_PARTIAL_MATURE_OR_FEE_LOSS_PROTOCOL_NO_DEADLINE_EXTENSION",
    "T1_ACCEPTANCE_IS_NOT_CURRENT_CASH_SETTLEMENT_REQUIRES_ORIGINAL_BANK_RECEIPT",
    "LIQUIDITY_RISK_UNCOVERED_CHECKPOINTS_REMAIN_EVEN_WHEN_ONE_POSITION_CAN_BE_PREPARED",
]


def lookup_next_whole_recovery(
    session: Session, user_id: UUID, key: str, now: datetime
) -> FullRecoveryNextLookup:
    _read_snapshot(session)
    now = _now(now)
    try:
        forwarded_key = next_whole_v1_key(key)
    except ValueError as error:
        raise PolicyLifecycleError("INVALID_NEXT_WHOLE_KEY", str(error), 422) from error
    original = lookup_full_recovery_execution(session, user_id, forwarded_key, now)
    if original.user_id != user_id or original.idempotency_key != forwarded_key:
        raise PolicyLifecycleError("NEXT_WHOLE_ORIGINAL_INVALID", "原owner/键身份未匹配", 409)
    bound = None
    if original.status == "RECORDED":
        body = original.original_request
        if (
            body is None
            or original.action is None
            or original.original_action_request is None
            or original.user_id != user_id
            or original.idempotency_key != forwarded_key
            or body.idempotency_key != forwarded_key
            or original.client_request_hash != configuration_hash(body.model_dump(mode="json"))
            or original.server_request_hash != configuration_hash(original.original_action_request)
        ):
            raise PolicyLifecycleError("NEXT_WHOLE_ORIGINAL_INVALID", "原完整v1请求/键未匹配", 409)
        bound = FullRecoveryNextRequest(
            policy_id=body.policy_id,
            expected_version_id=body.expected_version_id,
            expected_epoch_id=body.expected_epoch_id,
            idempotency_key=key,
        )
    elif any(
        value is not None
        for value in (
            original.original_request,
            original.action,
            original.original_action_request,
            original.client_request_hash,
            original.server_request_hash,
            original.original_consent,
        )
    ):
        raise PolicyLifecycleError("NEXT_WHOLE_ORIGINAL_INVALID", "未找到不能携带原成功记录", 409)
    return FullRecoveryNextLookup(
        user_id=user_id,
        idempotency_key=key,
        status=original.status,
        bound_request=bound,
        bound_request_hash=configuration_hash(bound.model_dump(mode="json")) if bound else None,
        original_v1_lookup=original,
    )


def _match_original(original: FullRecoveryNextLookup, body: FullRecoveryNextRequest) -> None:
    if original.status == "RECORDED" and original.bound_request != body:
        raise PolicyLifecycleError("IDEMPOTENCY_CONFLICT", "原root-key不能改变完整身份请求", 409)


def preview_next_whole_recovery(
    session: Session, user_id: UUID, body: FullRecoveryNextRequest, now: datetime
) -> FullRecoveryNextPreview:
    _read_snapshot(session)
    now = _now(now)
    body = FullRecoveryNextRequest.model_validate_json(body.model_dump_json())
    existing = lookup_next_whole_recovery(session, user_id, body.idempotency_key, now)
    _match_original(existing, body)
    planning = None
    selection = None
    delegated = None
    status: Literal[
        "READY_TO_PREPARE",
        "BLOCKED",
        "UNKNOWN",
        "NO_ELIGIBLE_NEXT_POSITION",
        "ORIGINAL_ACTION_RETAINED",
    ] = "ORIGINAL_ACTION_RETAINED" if existing.status == "RECORDED" else "UNKNOWN"
    if existing.status != "RECORDED":
        full = read_full_policy(session, user_id, body.policy_id, now)
        if (
            full.policy_id != body.policy_id
            or full.template_name != "RecoveryPolicy"
            or full.current_version.version_id != body.expected_version_id
            or full.epoch_id != body.expected_epoch_id
        ):
            raise PolicyLifecycleError(
                "NEXT_WHOLE_CURRENT_IDENTITY_CHANGED", "当前用户/版本/轮次变更", 409
            )
        planning = read_full_recovery_planning(session, user_id, body.policy_id, now)
        if (planning.user_id, planning.policy_id, planning.as_of) != (user_id, body.policy_id, now):
            raise PolicyLifecycleError(
                "NEXT_WHOLE_SOURCE_IDENTITY_CHANGED", "实际完整计划来源身份变更", 409
            )
        if (
            planning.state == "COMPUTED"
            and not planning.source_issues
            and planning.plan is not None
        ):
            selection = derive_next_whole_selection(body, planning.plan, user_id, now)
            status = "UNKNOWN" if planning.plan.status == "UNKNOWN" else "NO_ELIGIBLE_NEXT_POSITION"
            if selection.next_v1_request is not None:
                delegated = preview_full_recovery_execution(
                    session, user_id, selection.next_v1_request, now
                )
                if (
                    delegated.user_id != user_id
                    or delegated.original_request != selection.next_v1_request
                    or delegated.proof.user_id != user_id
                    or delegated.proof.epoch_id != body.expected_epoch_id
                    or delegated.proof.full_policy_version_id != body.expected_version_id
                    or delegated.proof.position_id != selection.next_v1_request.position_id
                    or delegated.proof.as_of != now
                    or delegated.proof.deadline_at != planning.plan.deadline_at
                    or delegated.execution_effect is not None
                    and (
                        delegated.proof.effect_hash
                        != execution_effect_hash(delegated.execution_effect)
                        or delegated.execution_effect.user_id != user_id
                        or delegated.execution_effect.position_id
                        != selection.next_v1_request.position_id
                    )
                ):
                    raise PolicyLifecycleError(
                        "NEXT_WHOLE_V1_PREVIEW_MISMATCH", "实际原v1预览未绑定选中范围", 409
                    )
                if (
                    delegated.proof.status == "VERIFIED_SCOPE"
                    and delegated.execution_effect is not None
                ):
                    status = "READY_TO_PREPARE"
                else:
                    status = "UNKNOWN" if delegated.proof.status == "UNKNOWN" else "BLOCKED"
    digest = configuration_hash(
        {
            "protocol": PROTOCOL,
            "user_id": str(user_id),
            "as_of": now.isoformat(),
            "request": body.model_dump(mode="json"),
            "existing": existing.model_dump(mode="json"),
            "planning": planning.model_dump(mode="json") if planning else None,
            "selection": selection.model_dump(mode="json") if selection else None,
            "original_v1_preview": delegated.model_dump(mode="json") if delegated else None,
        }
    )
    return FullRecoveryNextPreview(
        user_id=user_id,
        as_of=now,
        request=body,
        status=status,
        planning=planning,
        selection=selection,
        original_v1_preview=delegated,
        existing=existing,
        input_hash=digest,
        limitations=LIMITATIONS,
    )


def prepare_next_whole_recovery(
    engine: Engine,
    user_id: UUID,
    body: FullRecoveryNextRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> ActionResponse:
    now = _now(now)
    body = FullRecoveryNextRequest.model_validate_json(body.model_dump_json())
    try:
        require_local_user(principal, user_id, now)
    except ValueError as error:
        raise PolicyLifecycleError("NEXT_WHOLE_CURRENT_USER_REQUIRED", str(error), 403) from error
    with audit_command_guard(engine, user_id):
        with _reader(engine) as read:
            preview = preview_next_whole_recovery(read, user_id, body, now)
            if preview.existing.status == "RECORDED":
                original = preview.existing.original_v1_lookup.action
                assert original is not None  # checked by exact original lookup
                return original
            if preview.status != "READY_TO_PREPARE" or preview.selection is None:
                raise PolicyLifecycleError("NEXT_WHOLE_NOT_READY", preview.status, 409)
            forwarded = preview.selection.next_v1_request
            assert forwarded is not None
        # The original private producer obtains its User lock, recomputes all
        # current facts and enforces original idempotency, ASK and bank guards.
        # No v2 code writes balances, source Evidence, Action, consent or receipt.
        return prepare_full_recovery_execution(engine, user_id, forwarded, principal, now)
